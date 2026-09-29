/*
    Minesweeper (PostgreSQL / PL/pgSQL edition)

    Classic Minesweeper played through SQL. start_minesweeper() builds a fresh
    9x9 board with 10 hidden mines; click_cell(x, y) reveals a cell (cascading
    through connected zero-adjacency cells, like a real flood fill) and prints
    the board via RAISE NOTICE.

    Usage:
        CALL start_minesweeper();
        CALL click_cell(5, 5);
*/
DROP TABLE IF EXISTS ms_board CASCADE;

CREATE TABLE ms_board (
    x INT, 
    y INT, 
    is_mine BOOLEAN DEFAULT FALSE, 
    is_revealed BOOLEAN DEFAULT FALSE, 
    is_flagged BOOLEAN DEFAULT FALSE, 
    adjacent_mines INT DEFAULT 0
);

CREATE OR REPLACE PROCEDURE start_minesweeper()
LANGUAGE plpgsql AS $$
BEGIN
    TRUNCATE TABLE ms_board;

    -- 1. Generate a 9x9 Grid using generate_series
    INSERT INTO ms_board (x, y)
    SELECT gx.x, gy.y 
    FROM generate_series(1, 9) gx(x), generate_series(1, 9) gy(y);

    -- 2. Randomly plant 10 mines
    UPDATE ms_board 
    SET is_mine = TRUE 
    FROM (SELECT x, y FROM ms_board ORDER BY random() LIMIT 10) rnd
    WHERE ms_board.x = rnd.x AND ms_board.y = rnd.y;

    -- 3. Precalculate adjacent mines for all safe cells using a spatial bounding box
    UPDATE ms_board b
    SET adjacent_mines = (
        SELECT COUNT(*) FROM ms_board m 
        WHERE m.is_mine = TRUE 
          AND ABS(m.x - b.x) <= 1 AND ABS(m.y - b.y) <= 1
    )
    WHERE b.is_mine = FALSE;
    
    RAISE NOTICE 'New game started. 10 mines hidden.';
END;
$$;

---

CREATE OR REPLACE VIEW vw_minesweeper AS
SELECT 
    0 AS sort_key, '   1  2  3  4  5  6  7  8  9 ' AS board_row
UNION ALL
SELECT 
    y AS sort_key,
    y::TEXT || ' ' || string_agg(
        CASE 
            WHEN is_flagged THEN '[F]'
            WHEN NOT is_revealed THEN ' . '
            WHEN is_mine AND is_revealed THEN ' * '
            WHEN adjacent_mines = 0 THEN '   '
            ELSE ' ' || adjacent_mines::TEXT || ' '
        END, '' ORDER BY x
    ) AS board_row
FROM ms_board
GROUP BY y;

---
CREATE OR REPLACE PROCEDURE click_cell(p_x INT, p_y INT)
LANGUAGE plpgsql AS $$
DECLARE
    rows_affected INT;
    unrevealed_safe_count INT;
    hit_mine BOOLEAN;
    board_output RECORD;
BEGIN
    -- Ignore clicks on flagged or already revealed cells
    IF EXISTS (SELECT 1 FROM ms_board WHERE x = p_x AND y = p_y AND (is_flagged OR is_revealed)) THEN
        RETURN;
    END IF;

    -- Did we hit a mine?
    SELECT is_mine INTO hit_mine FROM ms_board WHERE x = p_x AND y = p_y;

    IF hit_mine THEN
        -- Game Over: Reveal all mines
        UPDATE ms_board SET is_revealed = TRUE WHERE is_mine = TRUE;
        RAISE NOTICE '>>> BOOM! You hit a mine. Game Over. <<<';
    ELSE
        -- Reveal the specific cell the player clicked
        UPDATE ms_board SET is_revealed = TRUE WHERE x = p_x AND y = p_y;

        -- THE CASCADE: Relational Flood Fill
        LOOP
            UPDATE ms_board unrev
            SET is_revealed = TRUE
            FROM ms_board rev 
            WHERE rev.is_revealed = TRUE AND rev.adjacent_mines = 0
              AND unrev.is_revealed = FALSE AND unrev.is_flagged = FALSE
              AND ABS(unrev.x - rev.x) <= 1 AND ABS(unrev.y - rev.y) <= 1;

            -- Check if any new cells were cascaded. If not, break the loop.
            GET DIAGNOSTICS rows_affected = ROW_COUNT;
            EXIT WHEN rows_affected = 0;
        END LOOP;
        
        -- Win Condition: Are all non-mine cells revealed?
        SELECT COUNT(*) INTO unrevealed_safe_count FROM ms_board WHERE is_mine = FALSE AND is_revealed = FALSE;
        IF unrevealed_safe_count = 0 THEN
            RAISE NOTICE '>>> CONGRATULATIONS! You cleared the minefield! <<<';
        END IF;
    END IF;

    -- Output the current board state to the Messages console
    RAISE NOTICE ' ';
    FOR board_output IN SELECT board_row FROM vw_minesweeper ORDER BY sort_key LOOP
        RAISE NOTICE '%', board_output.board_row;
    END LOOP;
END;
$$;

---
-- Generate a new board CALL start_minesweeper();
-- Take a wild guess in the middle of the board  CALL click_cell(5, 5);