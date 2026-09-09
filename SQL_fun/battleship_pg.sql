/*
    Battleship (PostgreSQL / PL/pgSQL edition)

    A single-player Battleship game played entirely through SQL. Ships are
    pre-placed, then each shot is fired by calling the fire_shot() procedure,
    which updates game state and prints the current combat map via RAISE NOTICE.

    Usage:
        CALL fire_shot(1, 3, 3);   -- fire at column 3, row 3 of game 1
        CALL fire_shot(1, 5, 5);
*/

-- 1. DDL: Create Tables & Initialize State
DROP TABLE IF EXISTS shots_fired CASCADE;
DROP TABLE IF EXISTS ship_placements CASCADE;
DROP TABLE IF EXISTS games CASCADE;

CREATE TABLE games (
    game_id INT PRIMARY KEY,
    status VARCHAR(20)
);

CREATE TABLE ship_placements (
    game_id INT,
    ship_id INT,
    x INT,
    y INT,
    is_hit BOOLEAN DEFAULT FALSE
);

CREATE TABLE shots_fired (
    game_id INT,
    x INT,
    y INT,
    result VARCHAR(10)
);

-- Initialize Game 1
INSERT INTO games VALUES (1, 'ACTIVE');
-- Place a Carrier (5 cells) and a Submarine (3 cells)
INSERT INTO ship_placements (game_id, ship_id, x, y) VALUES
    (1, 1, 3, 3), (1, 1, 4, 3), (1, 1, 5, 3), (1, 1, 6, 3), (1, 1, 7, 3),
    (1, 2, 8, 7), (1, 2, 8, 8), (1, 2, 8, 9);

-- 2. DDL: Create the Rendering View
CREATE OR REPLACE VIEW vw_battleship_board AS
WITH numbers AS (
    -- 10x10 board coordinates
    SELECT generate_series(1, 10) AS n
),
games_list AS (SELECT DISTINCT game_id FROM games),
grid AS (
    -- Cross join to build every (x, y) cell for every active game
    SELECT gl.game_id, y.n AS y, x.n AS x
    FROM numbers y CROSS JOIN numbers x CROSS JOIN games_list gl
),
board_eval AS (
    SELECT
        g.game_id, g.y, g.x,
        -- Only ever reveal a ship cell if it has also been fired upon (no peeking)
        CASE
            WHEN sh.x IS NOT NULL AND s.x IS NOT NULL THEN '[X]'  -- confirmed hit
            WHEN sh.x IS NOT NULL AND s.x IS NULL THEN '( )'      -- shot fired, missed
            ELSE ' . '                                            -- unknown / unfired cell
        END AS cell_status
    FROM grid g
    LEFT JOIN shots_fired sh ON g.game_id = sh.game_id AND g.x = sh.x AND g.y = sh.y
    LEFT JOIN ship_placements s ON g.game_id = s.game_id AND g.x = s.x AND g.y = s.y
)
-- Header row with column numbers, then one row per y, prefixed with a letter (A, B, C...)
SELECT game_id, 0 AS sort_key, '   1  2  3  4  5  6  7  8  9  10' AS target_grid FROM games_list
UNION ALL
SELECT
    game_id,
    y AS sort_key,
    chr(64 + y) || ' ' || string_agg(cell_status, '' ORDER BY x)
FROM board_eval
GROUP BY game_id, y;

-- 3. DDL: Create the Game Loop Procedure
CREATE OR REPLACE PROCEDURE fire_shot(p_game_id INT, p_x INT, p_y INT)
LANGUAGE plpgsql AS $$
DECLARE
    v_target_ship_id INT;
    v_intact_cells INT;
    v_active_ships INT;
    board_output RECORD;
BEGIN
    IF EXISTS (SELECT 1 FROM shots_fired WHERE game_id = p_game_id AND x = p_x AND y = p_y) THEN
        RAISE NOTICE 'Coordinate already fired upon.';
    ELSE
        SELECT ship_id INTO v_target_ship_id
        FROM ship_placements WHERE game_id = p_game_id AND x = p_x AND y = p_y;

        IF v_target_ship_id IS NOT NULL THEN
            -- Hit: mark this cell of the ship as hit
            UPDATE ship_placements SET is_hit = TRUE WHERE game_id = p_game_id AND x = p_x AND y = p_y;

            SELECT COUNT(*) INTO v_intact_cells
            FROM ship_placements WHERE game_id = p_game_id AND ship_id = v_target_ship_id AND is_hit = FALSE;

            IF v_intact_cells = 0 THEN
                -- No cells left un-hit on this ship: it's sunk
                INSERT INTO shots_fired (game_id, x, y, result) VALUES (p_game_id, p_x, p_y, 'SUNK');
                RAISE NOTICE '>>> Hit and Sunk! <<<';

                SELECT COUNT(DISTINCT ship_id) INTO v_active_ships
                FROM ship_placements WHERE game_id = p_game_id AND is_hit = FALSE;

                IF v_active_ships = 0 THEN
                    -- Every ship in the fleet is sunk: game over
                    UPDATE games SET status = 'GAME OVER' WHERE game_id = p_game_id;
                    RAISE NOTICE '>>> All enemy ships destroyed. YOU WIN! <<<';
                END IF;
            ELSE
                INSERT INTO shots_fired (game_id, x, y, result) VALUES (p_game_id, p_x, p_y, 'HIT');
                RAISE NOTICE '> Direct Hit!';
            END IF;
        ELSE
            -- No ship at this coordinate
            INSERT INTO shots_fired (game_id, x, y, result) VALUES (p_game_id, p_x, p_y, 'MISS');
            RAISE NOTICE 'Miss.';
        END IF;
    END IF;

    -- Auto-render the board to the console (psql clients print RAISE NOTICE to stdout)
    RAISE NOTICE ' ';
    FOR board_output IN
        SELECT target_grid FROM vw_battleship_board WHERE game_id = p_game_id ORDER BY sort_key
    LOOP
        RAISE NOTICE '%', board_output.target_grid;
    END LOOP;
END;
$$;

-- PLAY THE GAME:
-- CALL fire_shot(1, 3, 3);
-- CALL fire_shot(1, 5, 5);
