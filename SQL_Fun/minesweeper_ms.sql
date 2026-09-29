/*
    Minesweeper (SQL Server / T-SQL edition)

    Classic Minesweeper played through SQL. Start_Minesweeper builds a fresh
    9x9 board with 10 hidden mines; ClickCell reveals a cell (cascading through
    connected zero-adjacency cells, like a real flood fill) and prints the board.

    Usage:
        EXEC Start_Minesweeper;
        EXEC ClickCell @x = 5, @y = 5;
*/
IF OBJECT_ID('MS_Board', 'U') IS NOT NULL DROP TABLE MS_Board;

CREATE TABLE MS_Board (
    x INT, 
    y INT, 
    is_mine BIT DEFAULT 0, 
    is_revealed BIT DEFAULT 0, 
    is_flagged BIT DEFAULT 0, 
    adjacent_mines INT DEFAULT 0
);
GO

CREATE OR ALTER PROCEDURE Start_Minesweeper AS
BEGIN
    SET NOCOUNT ON;
    DELETE FROM MS_Board;

    -- 1. Generate a 9x9 Grid
    WITH N AS (
        SELECT 1 AS n UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4 UNION ALL 
        SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL SELECT 9
    )
    INSERT INTO MS_Board (x, y)
    SELECT x.n, y.n FROM N x CROSS JOIN N y;

    -- 2. Randomly plant 10 mines using NEWID()
    WITH RandomMines AS (
        SELECT TOP (10) is_mine FROM MS_Board ORDER BY NEWID()
    )
    UPDATE RandomMines SET is_mine = 1;

    -- 3. Precalculate adjacent mines for all safe cells using a spatial bounding box
    UPDATE b
    SET adjacent_mines = (
        SELECT COUNT(*) FROM MS_Board m 
        WHERE m.is_mine = 1 
          AND ABS(m.x - b.x) <= 1 AND ABS(m.y - b.y) <= 1
    )
    FROM MS_Board b
    WHERE b.is_mine = 0;
    
    PRINT 'New game started. 10 mines hidden.';
END;
GO


----

CREATE OR ALTER VIEW vw_Minesweeper AS
SELECT 
    0 AS sort_key, '   1  2  3  4  5  6  7  8  9 ' AS board_row
UNION ALL
SELECT 
    y AS sort_key,
    CAST(y AS VARCHAR) + ' ' + STRING_AGG(
        CASE 
            WHEN is_flagged = 1 THEN '[F]'
            WHEN is_revealed = 0 THEN ' . '
            WHEN is_mine = 1 AND is_revealed = 1 THEN ' * '
            WHEN adjacent_mines = 0 THEN '   '
            ELSE ' ' + CAST(adjacent_mines AS VARCHAR) + ' '
        END, ''
    ) WITHIN GROUP (ORDER BY x) AS board_row
FROM MS_Board
GROUP BY y;
GO


----

CREATE OR ALTER PROCEDURE ClickCell 
    @x INT, 
    @y INT 
AS
BEGIN
    SET NOCOUNT ON;

    -- Ignore clicks on flagged or already revealed cells
    IF EXISTS (SELECT 1 FROM MS_Board WHERE x = @x AND y = @y AND (is_flagged = 1 OR is_revealed = 1))
        RETURN;

    -- Did we hit a mine?
    IF EXISTS (SELECT 1 FROM MS_Board WHERE x = @x AND y = @y AND is_mine = 1)
    BEGIN
        -- Game Over: Reveal all mines
        UPDATE MS_Board SET is_revealed = 1 WHERE is_mine = 1;
        PRINT '>>> BOOM! You hit a mine. Game Over. <<<';
    END
    ELSE
    BEGIN
        -- Reveal the specific cell the player clicked
        UPDATE MS_Board SET is_revealed = 1 WHERE x = @x AND y = @y;

        -- THE CASCADE: Relational Flood Fill
        -- Continuously reveal unrevealed cells adjacent to a revealed '0'
        WHILE (1=1)
        BEGIN
            UPDATE unrev
            SET is_revealed = 1
            FROM MS_Board unrev
            JOIN MS_Board rev 
              ON ABS(unrev.x - rev.x) <= 1 AND ABS(unrev.y - rev.y) <= 1
            WHERE rev.is_revealed = 1 AND rev.adjacent_mines = 0
              AND unrev.is_revealed = 0 AND unrev.is_flagged = 0;

            -- If the query didn't update any new cells, the cascade is finished
            IF @@ROWCOUNT = 0 BREAK;
        END
        
        -- Win Condition: Are all non-mine cells revealed?
        IF NOT EXISTS (SELECT 1 FROM MS_Board WHERE is_mine = 0 AND is_revealed = 0)
        BEGIN
            PRINT '>>> CONGRATULATIONS! You cleared the minefield! <<<';
        END
    END

    -- Output the current board state
    SELECT board_row AS [Minesweeper] FROM vw_Minesweeper ORDER BY sort_key;
END;
GO

-----

-- Gameplay  EXEC Start_Minesweeper;
-- Click row 5, column 5  EXEC ClickCell @x = 5, @y = 5;