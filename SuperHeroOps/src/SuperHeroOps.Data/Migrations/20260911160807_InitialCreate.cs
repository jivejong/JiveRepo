using System;
using Microsoft.EntityFrameworkCore.Migrations;
using Npgsql.EntityFrameworkCore.PostgreSQL.Metadata;

#nullable disable

namespace SuperHeroOps.Data.Migrations
{
    /// <inheritdoc />
    public partial class InitialCreate : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.CreateTable(
                name: "community_areas",
                columns: table => new
                {
                    id = table.Column<int>(type: "integer", nullable: false),
                    name = table.Column<string>(type: "text", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("pk_community_areas", x => x.id);
                });

            migrationBuilder.CreateTable(
                name: "heroes",
                columns: table => new
                {
                    id = table.Column<int>(type: "integer", nullable: false),
                    name = table.Column<string>(type: "text", nullable: false),
                    image_url = table.Column<string>(type: "text", nullable: false),
                    publisher = table.Column<string>(type: "text", nullable: true),
                    alignment = table.Column<string>(type: "text", nullable: true),
                    intelligence = table.Column<int>(type: "integer", nullable: false),
                    strength = table.Column<int>(type: "integer", nullable: false),
                    speed = table.Column<int>(type: "integer", nullable: false),
                    durability = table.Column<int>(type: "integer", nullable: false),
                    power = table.Column<int>(type: "integer", nullable: false),
                    combat = table.Column<int>(type: "integer", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("pk_heroes", x => x.id);
                });

            migrationBuilder.CreateTable(
                name: "crime_incidents",
                columns: table => new
                {
                    id = table.Column<long>(type: "bigint", nullable: false)
                        .Annotation("Npgsql:ValueGenerationStrategy", NpgsqlValueGenerationStrategy.IdentityByDefaultColumn),
                    case_number = table.Column<string>(type: "text", nullable: false),
                    occurred_at = table.Column<DateTime>(type: "timestamp without time zone", nullable: false),
                    primary_type = table.Column<string>(type: "text", nullable: false),
                    arrest = table.Column<bool>(type: "boolean", nullable: false),
                    latitude = table.Column<double>(type: "double precision", nullable: true),
                    longitude = table.Column<double>(type: "double precision", nullable: true),
                    community_area_id = table.Column<int>(type: "integer", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("pk_crime_incidents", x => x.id);
                    table.ForeignKey(
                        name: "fk_crime_incidents_community_areas_community_area_id",
                        column: x => x.community_area_id,
                        principalTable: "community_areas",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Restrict);
                });

            migrationBuilder.CreateTable(
                name: "hero_reports",
                columns: table => new
                {
                    id = table.Column<int>(type: "integer", nullable: false)
                        .Annotation("Npgsql:ValueGenerationStrategy", NpgsqlValueGenerationStrategy.IdentityByDefaultColumn),
                    community_area_id = table.Column<int>(type: "integer", nullable: false),
                    hero_id = table.Column<int>(type: "integer", nullable: false),
                    risk_assessment = table.Column<string>(type: "text", nullable: false),
                    predicted_impact_narrative = table.Column<string>(type: "text", nullable: false),
                    recommendation = table.Column<string>(type: "text", nullable: false),
                    generated_at = table.Column<DateTime>(type: "timestamp with time zone", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("pk_hero_reports", x => x.id);
                    table.ForeignKey(
                        name: "fk_hero_reports_community_areas_community_area_id",
                        column: x => x.community_area_id,
                        principalTable: "community_areas",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Restrict);
                    table.ForeignKey(
                        name: "fk_hero_reports_heroes_hero_id",
                        column: x => x.hero_id,
                        principalTable: "heroes",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Restrict);
                });

            migrationBuilder.CreateTable(
                name: "intervention_scores",
                columns: table => new
                {
                    id = table.Column<int>(type: "integer", nullable: false)
                        .Annotation("Npgsql:ValueGenerationStrategy", NpgsqlValueGenerationStrategy.IdentityByDefaultColumn),
                    community_area_id = table.Column<int>(type: "integer", nullable: false),
                    hero_id = table.Column<int>(type: "integer", nullable: false),
                    crime_category = table.Column<string>(type: "text", nullable: false),
                    normalized_score = table.Column<double>(type: "double precision", nullable: false),
                    projected_effect_percent = table.Column<double>(type: "double precision", nullable: false),
                    computed_at = table.Column<DateTime>(type: "timestamp with time zone", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("pk_intervention_scores", x => x.id);
                    table.ForeignKey(
                        name: "fk_intervention_scores_community_areas_community_area_id",
                        column: x => x.community_area_id,
                        principalTable: "community_areas",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Restrict);
                    table.ForeignKey(
                        name: "fk_intervention_scores_heroes_hero_id",
                        column: x => x.hero_id,
                        principalTable: "heroes",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Restrict);
                });

            migrationBuilder.CreateIndex(
                name: "ix_crime_incidents_community_area_id",
                table: "crime_incidents",
                column: "community_area_id");

            migrationBuilder.CreateIndex(
                name: "ix_crime_incidents_occurred_at",
                table: "crime_incidents",
                column: "occurred_at");

            migrationBuilder.CreateIndex(
                name: "ix_crime_incidents_primary_type",
                table: "crime_incidents",
                column: "primary_type");

            migrationBuilder.CreateIndex(
                name: "ix_hero_reports_community_area_id_hero_id",
                table: "hero_reports",
                columns: new[] { "community_area_id", "hero_id" });

            migrationBuilder.CreateIndex(
                name: "ix_hero_reports_hero_id",
                table: "hero_reports",
                column: "hero_id");

            migrationBuilder.CreateIndex(
                name: "ix_intervention_scores_community_area_id_hero_id",
                table: "intervention_scores",
                columns: new[] { "community_area_id", "hero_id" });

            migrationBuilder.CreateIndex(
                name: "ix_intervention_scores_hero_id",
                table: "intervention_scores",
                column: "hero_id");
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropTable(
                name: "crime_incidents");

            migrationBuilder.DropTable(
                name: "hero_reports");

            migrationBuilder.DropTable(
                name: "intervention_scores");

            migrationBuilder.DropTable(
                name: "community_areas");

            migrationBuilder.DropTable(
                name: "heroes");
        }
    }
}
