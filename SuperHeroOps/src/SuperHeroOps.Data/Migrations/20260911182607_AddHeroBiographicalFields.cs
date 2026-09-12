using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace SuperHeroOps.Data.Migrations
{
    /// <inheritdoc />
    public partial class AddHeroBiographicalFields : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.AddColumn<string>(
                name: "aliases",
                table: "heroes",
                type: "text",
                nullable: true);

            migrationBuilder.AddColumn<string>(
                name: "base",
                table: "heroes",
                type: "text",
                nullable: true);

            migrationBuilder.AddColumn<string>(
                name: "eye_color",
                table: "heroes",
                type: "text",
                nullable: true);

            migrationBuilder.AddColumn<string>(
                name: "first_appearance",
                table: "heroes",
                type: "text",
                nullable: true);

            migrationBuilder.AddColumn<string>(
                name: "full_name",
                table: "heroes",
                type: "text",
                nullable: true);

            migrationBuilder.AddColumn<string>(
                name: "group_affiliation",
                table: "heroes",
                type: "text",
                nullable: true);

            migrationBuilder.AddColumn<string>(
                name: "hair_color",
                table: "heroes",
                type: "text",
                nullable: true);

            migrationBuilder.AddColumn<string>(
                name: "height",
                table: "heroes",
                type: "text",
                nullable: true);

            migrationBuilder.AddColumn<string>(
                name: "occupation",
                table: "heroes",
                type: "text",
                nullable: true);

            migrationBuilder.AddColumn<string>(
                name: "place_of_birth",
                table: "heroes",
                type: "text",
                nullable: true);

            migrationBuilder.AddColumn<string>(
                name: "weight",
                table: "heroes",
                type: "text",
                nullable: true);
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropColumn(
                name: "aliases",
                table: "heroes");

            migrationBuilder.DropColumn(
                name: "base",
                table: "heroes");

            migrationBuilder.DropColumn(
                name: "eye_color",
                table: "heroes");

            migrationBuilder.DropColumn(
                name: "first_appearance",
                table: "heroes");

            migrationBuilder.DropColumn(
                name: "full_name",
                table: "heroes");

            migrationBuilder.DropColumn(
                name: "group_affiliation",
                table: "heroes");

            migrationBuilder.DropColumn(
                name: "hair_color",
                table: "heroes");

            migrationBuilder.DropColumn(
                name: "height",
                table: "heroes");

            migrationBuilder.DropColumn(
                name: "occupation",
                table: "heroes");

            migrationBuilder.DropColumn(
                name: "place_of_birth",
                table: "heroes");

            migrationBuilder.DropColumn(
                name: "weight",
                table: "heroes");
        }
    }
}
