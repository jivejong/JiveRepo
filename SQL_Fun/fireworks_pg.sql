/*
    ASCII Fireworks (PostgreSQL)

    Renders three overlapping firework bursts as ASCII art on a 101x51 grid.
    Each burst is a ring (via distance from its center) that is only drawn
    where a periodic sin/cos function of the angle (via ATAN2) crosses a
    threshold, which breaks the ring into radiating petal/spark shapes instead
    of a solid circle. Cells outside any burst get a deterministic pseudo-random
    "sparkle" dot so the background isn't empty. Run as a plain SELECT.
*/
WITH numbers AS (
    SELECT generate_series(0, 100) AS n
),
fireworks AS (
    SELECT
        x.n AS x,
        y.n AS y,
        -- Which (if any) firework burst this cell belongs to.
        -- Each burst: (1) must be within `radius` of its center, forming a ring,
        -- and (2) ATAN2(dy, dx) gives the angle from the center, which is fed
        -- into sin/cos at some frequency; only angles where |sin|/|cos| < 0.3
        -- are drawn, carving the ring into evenly spaced radiating spikes.
        CASE
            -- Burst 1 at (30, 20): 5 spikes (sin, frequency 5)
            WHEN SQRT(POWER(x.n - 30, 2) + POWER(y.n - 20, 2)) < 15
                 AND ABS(SIN(
                    CASE
                        WHEN x.n = 30 AND y.n = 20 THEN 0  -- avoid ATAN2(0,0) undefined angle at dead center
                        ELSE ATAN2(y.n - 20, x.n - 30)
                    END * 5)) < 0.3 THEN 1
            -- Burst 2 at (60, 15): 4 spikes (cos, frequency 4)
            WHEN SQRT(POWER(x.n - 60, 2) + POWER(y.n - 15, 2)) < 12
                 AND ABS(COS(
                    CASE
                        WHEN x.n = 60 AND y.n = 15 THEN 0
                        ELSE ATAN2(y.n - 15, x.n - 60)
                    END * 4)) < 0.3 THEN 2
            -- Burst 3 at (45, 35): 6 spikes (sin, frequency 6), phase-shifted by 1.2 rad
            WHEN SQRT(POWER(x.n - 45, 2) + POWER(y.n - 35, 2)) < 10
                 AND ABS(SIN(
                    CASE
                        WHEN x.n = 45 AND y.n = 35 THEN 0
                        ELSE ATAN2(y.n - 35, x.n - 45)
                    END * 6 + 1.2)) < 0.3 THEN 3
            ELSE 0
        END AS burst_type,
        -- Deterministic "random" sparkle: no actual randomness, just a chaotic-looking
        -- sine function of the coordinates, bucketed into 4 density levels (0-3)
        CAST(ABS(SIN(x.n * 1.7 + y.n * 2.3)) * 4 AS INT) AS sparkle
    FROM numbers x
    CROSS JOIN numbers y
    WHERE x.n < 101 AND y.n < 51
)
-- Render: burst cells take priority over sparkle, each burst gets its own glyph,
-- and background sparkle density maps to characters of decreasing "brightness"
SELECT
    string_agg(
        CASE
            WHEN burst_type = 1 THEN '✦'
            WHEN burst_type = 2 THEN '✧'
            WHEN burst_type = 3 THEN '★'
            WHEN sparkle = 3 THEN '·'
            WHEN sparkle = 2 THEN '°'
            WHEN sparkle = 1 THEN '•'
            ELSE ' '
        END,
        ''
    ) AS fireworks
FROM fireworks
GROUP BY y
ORDER BY y DESC;  -- DESC so higher y (up) prints first: y=0 is the bottom row of the sky