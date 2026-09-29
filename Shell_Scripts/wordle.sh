#!/usr/bin/env bash

# ANSI Color Codes
GREEN='\033[1;42;37m'
YELLOW='\033[1;43;30m'
GRAY='\033[1;100;37m'
RESET='\033[0m'

# Built-in word list (can be replaced with an external dictionary file)
WORDS=("CRANE" "SLATE" "ROBOT" "BOOST" "GHOST" "PLANT" "LIGHT" "SHINE" "DRIVE" "FLAME")

# Pick random target
TARGET="${WORDS[$((RANDOM % ${#WORDS[@]}))]}"
MAX_ATTEMPTS=6
ATTEMPT=1

echo "==============================="
echo "        CLI WORDLE             "
echo "  Guess the 5-letter word!     "
echo "==============================="

while (( ATTEMPT <= MAX_ATTEMPTS )); do
    read -rp "Attempt $ATTEMPT/$MAX_ATTEMPTS: " GUESS
    GUESS=$(echo "$GUESS" | tr '[:lower:]' '[:upper:]')

    # Input validation
    if [[ ! "$GUESS" =~ ^[A-Z]{5}$ ]]; then
        echo "Error: Word must be exactly 5 letters."
        continue
    fi

    # Initialize tracking arrays
    declare -a RESULT=("" "" "" "" "")
    declare -A TARGET_COUNTS

    # Tally target letter frequencies
    for (( i=0; i<5; i++ )); do
        c="${TARGET:$i:1}"
        (( TARGET_COUNTS["$c"]++ ))
    done

    # Pass 1: Find Greens (Exact Matches)
    for (( i=0; i<5; i++ )); do
        g="${GUESS:$i:1}"
        t="${TARGET:$i:1}"
        if [[ "$g" == "$t" ]]; then
            RESULT[$i]="GREEN"
            (( TARGET_COUNTS["$g"]-- ))
        fi
    done

    # Pass 2: Find Yellows and Grays
    for (( i=0; i<5; i++ )); do
        if [[ -z "${RESULT[$i]}" ]]; then
            g="${GUESS:$i:1}"
            if (( TARGET_COUNTS["$g"] > 0 )); then
                RESULT[$i]="YELLOW"
                (( TARGET_COUNTS["$g"]-- ))
            else
                RESULT[$i]="GRAY"
            fi
        fi
    done

    # Render styled tiles
    OUTPUT=""
    for (( i=0; i<5; i++ )); do
        char="${GUESS:$i:1}"
        case "${RESULT[$i]}" in
            "GREEN")  OUTPUT+="${GREEN} $char ${RESET} " ;;
            "YELLOW") OUTPUT+="${YELLOW} $char ${RESET} " ;;
            "GRAY")   OUTPUT+="${GRAY} $char ${RESET} " ;;
        esac
    done
    echo -e "Feedback: $OUTPUT\n"

    # Check win/loss
    if [[ "$GUESS" == "$TARGET" ]]; then
        echo "Splendid! You won in $ATTEMPT attempts!"
        exit 0
    fi

    (( ATTEMPT++ ))
done

echo "Game over! The word was: $TARGET"