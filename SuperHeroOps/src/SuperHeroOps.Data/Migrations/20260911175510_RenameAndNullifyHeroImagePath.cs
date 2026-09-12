using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace SuperHeroOps.Data.Migrations
{
    /// <inheritdoc />
    public partial class RenameAndNullifyHeroImagePath : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropColumn(
                name: "image_url",
                table: "heroes");

            migrationBuilder.AddColumn<string>(
                name: "image_path",
                table: "heroes",
                type: "text",
                nullable: true);
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropColumn(
                name: "image_path",
                table: "heroes");

            migrationBuilder.AddColumn<string>(
                name: "image_url",
                table: "heroes",
                type: "text",
                nullable: false,
                defaultValue: "");
        }
    }
}
