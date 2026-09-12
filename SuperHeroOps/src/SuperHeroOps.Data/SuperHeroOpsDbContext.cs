using Microsoft.EntityFrameworkCore;
using SuperHeroOps.Core;

namespace SuperHeroOps.Data;

public class SuperHeroOpsDbContext(DbContextOptions<SuperHeroOpsDbContext> options) : DbContext(options)
{
    public DbSet<CommunityArea> CommunityAreas => Set<CommunityArea>();
    public DbSet<CrimeIncident> CrimeIncidents => Set<CrimeIncident>();
    public DbSet<Hero> Heroes => Set<Hero>();
    public DbSet<InterventionScore> InterventionScores => Set<InterventionScore>();
    public DbSet<HeroReport> HeroReports => Set<HeroReport>();

    protected override void OnModelCreating(ModelBuilder modelBuilder)
    {
        modelBuilder.Entity<CommunityArea>(entity =>
        {
            entity.HasKey(e => e.Id);
            entity.Property(e => e.Id).ValueGeneratedNever();
        });

        modelBuilder.Entity<CrimeIncident>(entity =>
        {
            entity.HasOne(e => e.CommunityArea)
                .WithMany(a => a.Incidents)
                .HasForeignKey(e => e.CommunityAreaId)
                .OnDelete(DeleteBehavior.Restrict);

            // Socrata's crime dates are naive Chicago wall-clock timestamps with no zone
            // info, so store as `timestamp` (not `timestamptz`) - avoids Npgsql's
            // DateTimeKind.Utc requirement for timestamptz columns.
            entity.Property(e => e.OccurredAt).HasColumnType("timestamp without time zone");

            entity.HasIndex(e => e.CommunityAreaId);
            entity.HasIndex(e => e.PrimaryType);
            entity.HasIndex(e => e.OccurredAt);
        });

        modelBuilder.Entity<Hero>(entity =>
        {
            entity.HasKey(e => e.Id);
            entity.Property(e => e.Id).ValueGeneratedNever();
        });

        modelBuilder.Entity<InterventionScore>(entity =>
        {
            entity.HasOne(e => e.CommunityArea)
                .WithMany(a => a.Scores)
                .HasForeignKey(e => e.CommunityAreaId)
                .OnDelete(DeleteBehavior.Restrict);

            entity.HasOne(e => e.Hero)
                .WithMany(h => h.Scores)
                .HasForeignKey(e => e.HeroId)
                .OnDelete(DeleteBehavior.Restrict);

            entity.HasIndex(e => new { e.CommunityAreaId, e.HeroId });
        });

        modelBuilder.Entity<HeroReport>(entity =>
        {
            entity.HasOne(e => e.CommunityArea)
                .WithMany(a => a.Reports)
                .HasForeignKey(e => e.CommunityAreaId)
                .OnDelete(DeleteBehavior.Restrict);

            entity.HasOne(e => e.Hero)
                .WithMany(h => h.Reports)
                .HasForeignKey(e => e.HeroId)
                .OnDelete(DeleteBehavior.Restrict);

            entity.HasIndex(e => new { e.CommunityAreaId, e.HeroId });
        });
    }
}
