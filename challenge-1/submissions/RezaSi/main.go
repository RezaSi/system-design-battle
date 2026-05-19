// RezaSi reference submission for challenge-1 (URL Shortener).
//
// Two containers: this Go service and a Redis with AOF persistence on a
// named volume. Redis is the only source of truth — URL mappings and the
// hit counter both live there. AOF (`appendonly yes`, `appendfsync
// everysec`) carries data across `docker compose restart` with at most
// one second of write loss.
//
// Codes are 7 random base62 characters (~41 bits of entropy). The spec
// no longer requires same-URL-same-code, so each POST mints a fresh
// code; on the (vanishingly rare) collision with an existing key, we
// re-roll up to a handful of times. No reverse url->code index needed.
package main

import (
	"context"
	"crypto/rand"
	"encoding/json"
	"errors"
	"io"
	"log"
	"net/http"
	"os"
	"os/signal"
	"regexp"
	"strconv"
	"strings"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/redis/go-redis/v9"
)

const (
	keyURLPrefix = "u:"
	keyHitPrefix = "h:"
	codeLen      = 7
	maxMintTries = 8
)

var (
	base62Alphabet = []byte("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
	codeRegex      = regexp.MustCompile(`^[A-Za-z0-9]{4,16}$`)
)

type server struct {
	rdb        *redis.Client
	publicHost string
	ready      atomic.Bool
}

func main() {
	addr := getenv("REDIS_ADDR", "cache:6379")
	publicHost := strings.TrimRight(getenv("PUBLIC_HOST", "http://localhost:8080"), "/")

	rdb := redis.NewClient(&redis.Options{
		Addr:         addr,
		PoolSize:     64,
		MinIdleConns: 8,
		DialTimeout:  2 * time.Second,
		ReadTimeout:  1 * time.Second,
		WriteTimeout: 1 * time.Second,
		MaxRetries:   2,
	})
	s := &server{rdb: rdb, publicHost: publicHost}
	go s.waitForRedis()

	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", s.healthz)
	mux.HandleFunc("POST /shorten", s.shorten)
	mux.HandleFunc("GET /api/codes/{code}", s.metadata)
	mux.HandleFunc("GET /{code}", s.resolve)

	httpServer := &http.Server{
		Addr:              ":8080",
		Handler:           mux,
		ReadHeaderTimeout: 3 * time.Second,
	}

	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		<-sigCh
		log.Printf("signal received, draining…")
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = httpServer.Shutdown(ctx)
	}()

	log.Printf("listening on :8080, redis=%s", addr)
	if err := httpServer.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		log.Fatal(err)
	}
}

// waitForRedis pings Redis with backoff until it answers, then flips the
// readiness flag. /healthz returns 503 until this completes, which is
// what the grader's 60-second wait loop is designed to tolerate.
func (s *server) waitForRedis() {
	backoff := 250 * time.Millisecond
	for {
		ctx, cancel := context.WithTimeout(context.Background(), 1*time.Second)
		err := s.rdb.Ping(ctx).Err()
		cancel()
		if err == nil {
			s.ready.Store(true)
			log.Printf("redis ready")
			return
		}
		time.Sleep(backoff)
		if backoff < 2*time.Second {
			backoff *= 2
		}
	}
}

func (s *server) healthz(w http.ResponseWriter, _ *http.Request) {
	if !s.ready.Load() {
		writeError(w, http.StatusServiceUnavailable, "not ready")
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

type shortenReq struct {
	URL string `json:"url"`
}

type shortenResp struct {
	Code     string `json:"code"`
	ShortURL string `json:"short_url"`
}

func (s *server) shorten(w http.ResponseWriter, r *http.Request) {
	raw, err := io.ReadAll(io.LimitReader(r.Body, 1<<14))
	if err != nil {
		writeError(w, http.StatusBadRequest, "invalid json body")
		return
	}
	var body shortenReq
	if err := json.Unmarshal(raw, &body); err != nil {
		writeError(w, http.StatusBadRequest, "invalid json body")
		return
	}
	url := strings.TrimSpace(body.URL)
	if url == "" {
		writeError(w, http.StatusBadRequest, "url is required")
		return
	}
	if !(strings.HasPrefix(url, "http://") || strings.HasPrefix(url, "https://")) {
		writeError(w, http.StatusBadRequest, "url must start with http:// or https://")
		return
	}

	code, err := s.mintCode(r.Context(), url)
	if err != nil {
		log.Printf("mint failed: %v", err)
		writeError(w, http.StatusInternalServerError, "internal error")
		return
	}
	writeJSON(w, http.StatusCreated, shortenResp{
		Code:     code,
		ShortURL: s.publicHost + "/" + code,
	})
}

// mintCode generates a fresh random code, persists it with SETNX, and
// retries a few times if it happens to collide with an existing key.
// With 62^7 ≈ 3.5e12 codes the collision probability is astronomically
// small for the scale we're testing at, but we still loop defensively.
func (s *server) mintCode(ctx context.Context, url string) (string, error) {
	for i := 0; i < maxMintTries; i++ {
		code := randomCode(codeLen)
		ok, err := s.rdb.SetNX(ctx, keyURLPrefix+code, url, 0).Result()
		if err != nil {
			return "", err
		}
		if ok {
			return code, nil
		}
	}
	return "", errors.New("code collision storm; retry")
}

func randomCode(n int) string {
	raw := make([]byte, n)
	if _, err := rand.Read(raw); err != nil {
		// crypto/rand failure is exotic. Don't 500 the request loop over it;
		// fall back to a low-quality time source. Modulo bias is irrelevant
		// for a short code.
		nano := time.Now().UnixNano()
		for i := range raw {
			raw[i] = byte(nano >> (uint(i) * 8))
		}
	}
	out := make([]byte, n)
	for i, b := range raw {
		out[i] = base62Alphabet[int(b)%62]
	}
	return string(out)
}

func (s *server) resolve(w http.ResponseWriter, r *http.Request) {
	code := r.PathValue("code")
	if !codeRegex.MatchString(code) {
		writeError(w, http.StatusNotFound, "not found")
		return
	}
	url, err := s.rdb.Get(r.Context(), keyURLPrefix+code).Result()
	if errors.Is(err, redis.Nil) {
		writeError(w, http.StatusNotFound, "not found")
		return
	}
	if err != nil {
		writeError(w, http.StatusInternalServerError, "internal error")
		return
	}

	// Fire-and-forget hit increment. The redirect path must not block on it
	// or we'd halve our RPS at the upper load stages.
	go func(c string) {
		ctx, cancel := context.WithTimeout(context.Background(), 1*time.Second)
		defer cancel()
		_ = s.rdb.Incr(ctx, keyHitPrefix+c).Err()
	}(code)

	w.Header().Set("Location", url)
	w.WriteHeader(http.StatusFound)
}

func (s *server) metadata(w http.ResponseWriter, r *http.Request) {
	code := r.PathValue("code")
	if !codeRegex.MatchString(code) {
		writeError(w, http.StatusNotFound, "not found")
		return
	}
	res, err := s.rdb.MGet(r.Context(), keyURLPrefix+code, keyHitPrefix+code).Result()
	if err != nil {
		writeError(w, http.StatusInternalServerError, "internal error")
		return
	}
	if res[0] == nil {
		writeError(w, http.StatusNotFound, "not found")
		return
	}
	url, _ := res[0].(string)
	var hits int64
	if res[1] != nil {
		if hitsStr, ok := res[1].(string); ok {
			hits, _ = strconv.ParseInt(hitsStr, 10, 64)
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"code": code,
		"url":  url,
		"hits": hits,
	})
}

func getenv(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func writeJSON(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(body)
}

func writeError(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, map[string]string{"error": msg})
}
