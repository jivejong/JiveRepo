/*
    ASCII Sine Wave (PostgreSQL)

    Renders an animated-looking sine wave as ASCII/block art by computing,
    for every column x, the row (wave_y) the wave curve passes through, then
    shading each cell based on its position relative to that curve. Run as a
    plain SELECT.
*/
WITH params AS (
    SELECT 80 AS width, 30 AS height
),
points AS (
    SELECT
        x,
        y,
        -- Wave center is row 20 (of 30), amplitude 10 rows.
        -- x/5.0 controls horizontal wavelength; y/3.0 skews the wave diagonally.
        (20 + 10 * SIN(x::float / 5.0 + y::float / 3.0))::int AS wave_y
    FROM generate_series(0, (SELECT width FROM params)) AS x,
         generate_series(0, (SELECT height FROM params)) AS y
)
SELECT
    string_agg(
        CASE
            -- Within 1 row of the wave curve: solid block on the curve itself,
            -- lighter shade immediately above/below it
            WHEN y BETWEEN wave_y - 1 AND wave_y + 1 THEN
                CASE (y - wave_y + 1)::int
                    WHEN 0 THEN '█'
                    ELSE '▒'
                END
            WHEN y < wave_y THEN ' '  -- above the wave: empty sky
            ELSE '░'                  -- below the wave: light fill ("water")
        END,
        '' ORDER BY x
    ) AS wave_art
FROM points
GROUP BY y
ORDER BY y;