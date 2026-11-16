from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='AuditRun',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('account_id', models.UUIDField(db_index=True)),
                ('owner_name', models.CharField(max_length=80)),
                ('window_start', models.DateTimeField(blank=True, null=True)),
                ('window_end', models.DateTimeField(blank=True, null=True)),
                ('provider', models.CharField(max_length=20)),
                ('model', models.CharField(max_length=60)),
                ('provider_reason', models.CharField(blank=True, default='', max_length=255)),
                ('status', models.CharField(choices=[('VERIFIED', 'AI answer verified'), ('FALLBACK', 'Deterministic fallback'), ('REJECTED', 'Rejected')], max_length=10)),
                ('report', models.JSONField(default=dict)),
                ('verification', models.JSONField(default=dict)),
                ('attempts', models.JSONField(default=list)),
                ('evidence', models.JSONField(default=dict)),
                ('latency_ms', models.IntegerField(default=0)),
                ('closing_balance_tnd', models.CharField(default='0.000', max_length=24)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]

