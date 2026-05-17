#!/usr/bin/env bash
set -euo pipefail

usage() {
    echo "Usage: $0 <challenge-number>"
    echo "  Example: $0 1"
    exit 1
}

if [ $# -lt 1 ]; then
    echo "Error: missing challenge number."
    usage
fi

CHALLENGE="challenge-$1"

if [ ! -d "$CHALLENGE" ]; then
    echo "Error: challenge directory '$CHALLENGE' does not exist."
    exit 1
fi

if [ ! -d "$CHALLENGE/template" ]; then
    echo "Error: '$CHALLENGE/template' does not exist. Repo is incomplete."
    exit 1
fi

read -r -p "Enter your GitHub username: " USERNAME
if [ -z "$USERNAME" ]; then
    echo "Error: username cannot be empty."
    exit 1
fi

SUBMISSION_DIR="$CHALLENGE/submissions/$USERNAME"

if [ -d "$SUBMISSION_DIR" ]; then
    echo "Submission directory '$SUBMISSION_DIR' already exists."
    read -r -p "Overwrite with a fresh copy of the template? [y/N]: " ANSWER
    case "$ANSWER" in
        y|Y|yes|YES)
            rm -rf "$SUBMISSION_DIR"
            ;;
        *)
            echo "Leaving the existing submission folder in place."
            exit 0
            ;;
    esac
fi

mkdir -p "$SUBMISSION_DIR"
cp -R "$CHALLENGE/template/." "$SUBMISSION_DIR/"

cat <<EOF
Created '$SUBMISSION_DIR' from '$CHALLENGE/template/'.

Next steps:
  1. cd $SUBMISSION_DIR
  2. Edit the Dockerfile, docker-compose.yml, and app/ source.

     Every service you add to docker-compose.yml MUST declare cpus:
     and mem_limit:. The sum across services must fit the challenge's
     resource budget (see $CHALLENGE/README.md). The grader rejects
     the submission before running it if any service is missing a cap
     or the total goes over budget.

  3. Run the full local grader (same script CI runs):
       ./grade.sh $1 $USERNAME

  4. Commit and push:
       git checkout -b $CHALLENGE-solution
       git add $SUBMISSION_DIR
       git commit -m "$CHALLENGE: submission by $USERNAME"
       git push origin $CHALLENGE-solution
EOF
