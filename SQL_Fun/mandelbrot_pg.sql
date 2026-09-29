/*
    ASCII Mandelbrot Set (PostgreSQL)

    Renders the Mandelbrot fractal as ASCII art. A recursive CTE iterates the
    complex quadratic Z_(n+1) = Z_n^2 + C for every pixel in parallel, stopping
    each point once it escapes (|Z| > 2) or hits the iteration cap. Escape speed
    is mapped to a character ramp to approximate shading. Run as a plain SELECT.

    Note: this file is named "mandelbrot._pgsql" (non-standard extension);
    the SQL below is plain PostgreSQL and can be run in psql or pgAdmin
    regardless of the filename.
*/
WITH RECURSIVE
grid AS (
    -- Map screen coordinates to the complex plane
    -- X ranges from ~ -2.33 to 1.0 (Real part)
    -- Y ranges from ~ -1.25 to 1.25 (Imaginary part)
    SELECT 
        x AS x_idx,
        y AS y_idx,
        (x::float - 70.0) / 30.0 AS cx, 
        (y::float - 25.0) / 20.0 AS cy  
    FROM 
        generate_series(0, 100) AS x,
        generate_series(0, 50) AS y
),
mandelbrot AS (
    -- Base case: Z = 0
    SELECT x_idx, y_idx, cx, cy, 
           0.0::float AS zx, 
           0.0::float AS zy, 
           0 AS iter
    FROM grid
    
    UNION ALL
    
    -- Recursive step: Z_(n+1) = Z_n^2 + C
    SELECT m.x_idx, m.y_idx, m.cx, m.cy, 
           (m.zx * m.zx) - (m.zy * m.zy) + m.cx AS zx,
           (2.0 * m.zx * m.zy) + m.cy AS zy,
           m.iter + 1 AS iter
    FROM mandelbrot m
    -- Escape condition: stop if magnitude > 2 (squared > 4) or iterations hit 30
    WHERE (m.zx * m.zx) + (m.zy * m.zy) < 4.0 
    AND m.iter < 30
),
escape_iterations AS (
    -- Find the iteration count where each coordinate escaped
    SELECT x_idx, y_idx, MAX(iter) AS max_iter
    FROM mandelbrot
    GROUP BY x_idx, y_idx
)
-- Map iteration counts to character density and render rows
SELECT 
    string_agg(
        CASE 
            WHEN max_iter = 30 THEN ' ' -- Points inside the set are rendered as blank space
            ELSE SUBSTRING('.:-=+*#%@', (max_iter % 9) + 1, 1) 
        END, 
        '' ORDER BY x_idx
    ) AS mandelbrot_fractal
FROM escape_iterations
GROUP BY y_idx
ORDER BY y_idx;