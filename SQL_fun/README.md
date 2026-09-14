# SQL_fun

A collection of "just SQL" experiments — no application code, no ORM, just `SELECT`/`CALL` statements that render ASCII art or play small games directly in the query console. Everything here is meant to be pasted straight into a client (`psql`, `sqlcmd`, Azure Data Studio, pgAdmin, etc.) and run.

Files are suffixed `_pg` for PostgreSQL and `_ms` for SQL Server (T-SQL), since the two dialects diverge quite a bit for recursive CTEs, string aggregation, and stored procedures/functions.

## ASCII art (single-statement, no state)

These are one-shot `SELECT` statements — run the whole file and read the result set as a picture, one row of the grid per output row.

| File                                   | Engine     | What it draws                                                                                                                                                                              |
| -------------------------------------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| [circle_pg.sql](circle_pg.sql)         | PostgreSQL | A filled circle, using the circle equation `x^2 + y^2 = r^2` over a recursive CTE grid.                                                                                                    |
| [mandelbrot_pg.sql](mandelbrot_pg.sql) | PostgreSQL | The Mandelbrot set, iterating `Z = Z^2 + C` per pixel via a recursive CTE and shading by escape speed. Note the non-standard `._pgsql` file extension — the contents are plain PostgreSQL. |
| [sine_wave_pg.sql](sine_wave_pg.sql)   | PostgreSQL | An animated-looking sine wave rendered with block characters (`█ ▒ ░`).                                                                                                                    |
| [fireworks_pg.sql](fireworks_pg.sql)   | PostgreSQL | Three overlapping firework bursts, each a ring carved into radiating spikes via `ATAN2` + `SIN`/`COS`, plus background sparkle.                                                            |

## Games (stateful, multi-statement)

These create tables/views/procedures first, then you drive the game by
calling a procedure repeatedly. Each pair below is the same game implemented twice — once per SQL dialect.

| Game                                           | PostgreSQL                               | SQL Server                               |
| ---------------------------------------------- | ---------------------------------------- | ---------------------------------------- |
| Minesweeper (9x9, 10 mines, flood-fill reveal) | [minesweeper_pg.sql](minesweeper_pg.sql) | [minesweeper_ms.sql](minesweeper_ms.sql) |
| Battleship (10x10 board, Carrier + Submarine)  | [battleship_pg.sql](battleship_pg.sql)   | [battleship_ms.sql](battleship_ms.sql)   |

### Minesweeper

```sql
-- PostgreSQL
CALL start_minesweeper();
CALL click_cell(5, 5);

-- SQL Server
EXEC Start_Minesweeper;
EXEC ClickCell @x = 5, @y = 5;
```

### Battleship

```sql
-- PostgreSQL
CALL fire_shot(1, 3, 3);

-- SQL Server
EXEC FireShot @game_id = 1, @x = 3, @y = 3;
```

## Notes

- Each file is self-contained: run it top to bottom against an empty/scratch
  database or schema. The game scripts `DROP`/`TRUNCATE` their own tables at
  the top, so re-running a script resets that game.
- The PostgreSQL game scripts print board state with `RAISE NOTICE`, which
  shows up in your client's "Messages"/notice console, not the result grid —
  make sure that output is visible (e.g. `psql` shows it by default).
- The SQL Server game scripts print with `PRINT` and return the board as a
  result set from the same `EXEC` call.
