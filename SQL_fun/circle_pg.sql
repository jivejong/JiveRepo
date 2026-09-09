/*
    ASCII Circle (PostgreSQL)

    Draws a filled circle as ASCII art using nothing but a recursive CTE
    and the circle equation x^2 + y^2 = r^2. Run as a plain SELECT.
*/
WITH RECURSIVE y_vals(y) AS (
    -- Generate the Y-axis (vertical coordinates)
    SELECT -10
    UNION ALL
    SELECT y + 1 FROM y_vals WHERE y < 10
),
x_vals(x) AS (
    -- Generate the X-axis (horizontal coordinates)
    SELECT -20
    UNION ALL
    SELECT x + 1 FROM x_vals WHERE x < 20
)
SELECT 
    string_agg(
        -- The geometric formula for a circle: x^2 + y^2 = r^2
        -- We multiply y^2 by 4 to compensate for terminal fonts being taller than they are wide
        CASE WHEN (x * x) + (y * y * 4) <= 400 THEN '*' ELSE ' ' END,
        '' ORDER BY x
    ) AS ascii_circle
FROM y_vals CROSS JOIN x_vals
GROUP BY y
ORDER BY y;