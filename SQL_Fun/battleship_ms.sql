/*
    Battleship (SQL Server / T-SQL edition)

    A single-player Battleship game played entirely through SQL. Ships are
    pre-placed, then each shot is fired via EXEC FireShot, which updates game
    state and prints the current combat map.

    Usage:
        EXEC FireShot @game_id = 1, @x = 3, @y = 3;
        EXEC FireShot @game_id = 1, @x = 5, @y = 5;
*/

-- 1. DDL: Create Tables & Initialize State
IF OBJECT_ID('Shots_Fired', 'U') IS NOT NULL DROP TABLE Shots_Fired;
IF OBJECT_ID('Ship_Placements', 'U') IS NOT NULL DROP TABLE Ship_Placements;
IF OBJECT_ID('Games', 'U') IS NOT NULL DROP TABLE Games;

CREATE TABLE Games (
    game_id INT PRIMARY KEY, 
    status VARCHAR(20)
);

CREATE TABLE Ship_Placements (
    game_id INT, 
    ship_id INT, 
    x INT, 
    y INT, 
    is_hit BIT DEFAULT 0
);

CREATE TABLE Shots_Fired (
    game_id INT, 
    x INT, 
    y INT, 
    result VARCHAR(10)
);

-- Initialize Game 1
INSERT INTO Games VALUES (1, 'ACTIVE');
-- Place a Carrier (5 cells) and a Submarine (3 cells)
INSERT INTO Ship_Placements (game_id, ship_id, x, y) VALUES 
    (1, 1, 3, 3), (1, 1, 4, 3), (1, 1, 5, 3), (1, 1, 6, 3), (1, 1, 7, 3), 
    (1, 2, 8, 7), (1, 2, 8, 8), (1, 2, 8, 9); 
GO

-- 2. DDL: Create the Rendering View
CREATE OR ALTER VIEW vw_Battleship_Board AS
WITH numbers AS (
    SELECT 1 AS n UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4 UNION ALL SELECT 5 
    UNION ALL SELECT 6 UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL SELECT 9 UNION ALL SELECT 10
),
GamesList AS (SELECT DISTINCT game_id FROM Games),
grid AS (
    SELECT gl.game_id, y.n AS y, x.n AS x 
    FROM numbers y CROSS JOIN numbers x CROSS JOIN GamesList gl
),
board_eval AS (
    SELECT 
        g.game_id, g.y, g.x,
        CASE 
            WHEN sh.x IS NOT NULL AND s.x IS NOT NULL THEN '[X]'
            WHEN sh.x IS NOT NULL AND s.x IS NULL THEN '( )'
            ELSE ' . '
        END as cell_status
    FROM grid g
    LEFT JOIN Shots_Fired sh ON g.game_id = sh.game_id AND g.x = sh.x AND g.y = sh.y
    LEFT JOIN Ship_Placements s ON g.game_id = s.game_id AND g.x = s.x AND g.y = s.y
)
SELECT game_id, 0 AS sort_key, '   1  2  3  4  5  6  7  8  9  10' AS target_grid FROM GamesList
UNION ALL
SELECT 
    game_id, 
    y AS sort_key, 
    CHAR(64 + y) + ' ' + STRING_AGG(cell_status, '') WITHIN GROUP (ORDER BY x)
FROM board_eval
GROUP BY game_id, y;
GO

-- 3. DDL: Create the Game Loop Stored Procedure
CREATE OR ALTER PROCEDURE FireShot
    @game_id INT,
    @x INT,
    @y INT
AS
BEGIN
    SET NOCOUNT ON; 
    
    IF EXISTS (SELECT 1 FROM Shots_Fired WHERE game_id = @game_id AND x = @x AND y = @y)
    BEGIN
        PRINT 'Coordinate already fired upon.';
    END
    ELSE
    BEGIN
        DECLARE @target_ship_id INT;
        SELECT @target_ship_id = ship_id FROM Ship_Placements WHERE game_id = @game_id AND x = @x AND y = @y;

        IF @target_ship_id IS NOT NULL
        BEGIN
            UPDATE Ship_Placements SET is_hit = 1 WHERE game_id = @game_id AND x = @x AND y = @y;

            DECLARE @intact_cells INT;
            SELECT @intact_cells = COUNT(*) FROM Ship_Placements WHERE game_id = @game_id AND ship_id = @target_ship_id AND is_hit = 0;

            IF @intact_cells = 0
            BEGIN
                INSERT INTO Shots_Fired (game_id, x, y, result) VALUES (@game_id, @x, @y, 'SUNK');
                PRINT '>>> Hit and Sunk! <<<';
                
                DECLARE @active_ships INT;
                SELECT @active_ships = COUNT(DISTINCT ship_id) FROM Ship_Placements WHERE game_id = @game_id AND is_hit = 0;
                
                IF @active_ships = 0
                BEGIN
                    UPDATE Games SET status = 'GAME OVER' WHERE game_id = @game_id;
                    PRINT '>>> All enemy ships destroyed. YOU WIN! <<<';
                END
            END
            ELSE
            BEGIN
                INSERT INTO Shots_Fired (game_id, x, y, result) VALUES (@game_id, @x, @y, 'HIT');
                PRINT '> Direct Hit!';
            END
        END
        ELSE
        BEGIN
            INSERT INTO Shots_Fired (game_id, x, y, result) VALUES (@game_id, @x, @y, 'MISS');
            PRINT 'Miss.';
        END
    END

    -- Auto-Render the Board to the Results Grid
    SELECT target_grid AS [Combat Map]
    FROM vw_Battleship_Board 
    WHERE game_id = @game_id 
    ORDER BY sort_key;
END;
GO

-- PLAY THE GAME:
-- EXEC FireShot @game_id = 1, @x = 3, @y = 3;
-- EXEC FireShot @game_id = 1, @x = 5, @y = 5;