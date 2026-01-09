"""Phase V schema - add recurrence, due dates, priorities, tags, events, and notifications.

Revision ID: 001
Revises: None
Create Date: 2026-01-06

This migration adds all Phase V tables and columns:
- Task extensions: recurrence_type, recurrence_interval, next_occurrence_at, due_at, priority, parent_task_id
- TaskReminder table for scheduled reminders
- TaskTag and TaskTagAssociation tables for tagging
- TaskEvent table for event outbox
- AuditLog table for activity tracking
- NotificationDelivery table for notification delivery tracking
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create enums
    recurrence_type_enum = postgresql.ENUM('none', 'daily', 'weekly', 'custom', name='recurrencetype', create_type=False)
    priority_enum = postgresql.ENUM('low', 'medium', 'high', name='priority', create_type=False)
    reminder_status_enum = postgresql.ENUM('pending', 'sent', 'cancelled', 'failed', name='reminderstatus', create_type=False)
    event_type_enum = postgresql.ENUM(
        'task.created', 'task.updated', 'task.completed', 'task.deleted', 'task.recurred',
        name='taskeventtype', create_type=False
    )
    processing_status_enum = postgresql.ENUM('pending', 'processing', 'completed', 'failed', name='processingstatus', create_type=False)
    notification_channel_enum = postgresql.ENUM('email', 'push', 'in_app', name='notificationchannel', create_type=False)
    delivery_status_enum = postgresql.ENUM('pending', 'processing', 'sent', 'failed', name='deliverystatus', create_type=False)

    # Create enums in PostgreSQL
    op.execute("CREATE TYPE recurrencetype AS ENUM ('none', 'daily', 'weekly', 'custom')")
    op.execute("CREATE TYPE priority AS ENUM ('low', 'medium', 'high')")
    op.execute("CREATE TYPE reminderstatus AS ENUM ('pending', 'sent', 'cancelled', 'failed')")
    op.execute("CREATE TYPE taskeventtype AS ENUM ('task.created', 'task.updated', 'task.completed', 'task.deleted', 'task.recurred')")
    op.execute("CREATE TYPE processingstatus AS ENUM ('pending', 'processing', 'completed', 'failed')")
    op.execute("CREATE TYPE notificationchannel AS ENUM ('email', 'push', 'in_app')")
    op.execute("CREATE TYPE deliverystatus AS ENUM ('pending', 'processing', 'sent', 'failed')")

    # Add columns to tasks table (if they don't exist)
    # Using raw SQL for safer ADD COLUMN IF NOT EXISTS pattern
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='tasks' AND column_name='recurrence_type') THEN
                ALTER TABLE tasks ADD COLUMN recurrence_type recurrencetype DEFAULT 'none';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='tasks' AND column_name='recurrence_interval') THEN
                ALTER TABLE tasks ADD COLUMN recurrence_interval INTEGER;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='tasks' AND column_name='next_occurrence_at') THEN
                ALTER TABLE tasks ADD COLUMN next_occurrence_at TIMESTAMP;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='tasks' AND column_name='due_at') THEN
                ALTER TABLE tasks ADD COLUMN due_at TIMESTAMP;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='tasks' AND column_name='priority') THEN
                ALTER TABLE tasks ADD COLUMN priority priority DEFAULT 'medium';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='tasks' AND column_name='parent_task_id') THEN
                ALTER TABLE tasks ADD COLUMN parent_task_id UUID REFERENCES tasks(id);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='tasks' AND column_name='completed_at') THEN
                ALTER TABLE tasks ADD COLUMN completed_at TIMESTAMP;
            END IF;
        END $$;
    """)

    # Create index on due_at
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_tasks_due_at ON tasks(due_at);
    """)

    # Create task_reminders table
    op.execute("""
        CREATE TABLE IF NOT EXISTS task_reminders (
            id UUID PRIMARY KEY,
            task_id UUID NOT NULL REFERENCES tasks(id),
            user_id UUID NOT NULL REFERENCES users(id),
            remind_at TIMESTAMP NOT NULL,
            status reminderstatus DEFAULT 'pending',
            dapr_job_id VARCHAR(255),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            sent_at TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS ix_task_reminders_task_id ON task_reminders(task_id);
        CREATE INDEX IF NOT EXISTS ix_task_reminders_user_id ON task_reminders(user_id);
        CREATE INDEX IF NOT EXISTS ix_task_reminders_remind_at ON task_reminders(remind_at);
    """)

    # Create task_tags table
    op.execute("""
        CREATE TABLE IF NOT EXISTS task_tags (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES users(id),
            name VARCHAR(50) NOT NULL,
            color VARCHAR(7),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS ix_task_tags_user_id ON task_tags(user_id);
        CREATE INDEX IF NOT EXISTS ix_task_tags_name ON task_tags(name);
    """)

    # Create task_tag_associations table
    op.execute("""
        CREATE TABLE IF NOT EXISTS task_tag_associations (
            task_id UUID NOT NULL REFERENCES tasks(id),
            tag_id UUID NOT NULL REFERENCES task_tags(id),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (task_id, tag_id)
        );
    """)

    # Create task_events table (outbox)
    op.execute("""
        CREATE TABLE IF NOT EXISTS task_events (
            id UUID PRIMARY KEY,
            event_type taskeventtype NOT NULL,
            task_id UUID NOT NULL,
            user_id UUID NOT NULL,
            payload JSONB NOT NULL,
            cloudevents_id VARCHAR(255),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            published_at TIMESTAMP,
            processing_status processingstatus DEFAULT 'pending',
            processed_at TIMESTAMP,
            retry_count INTEGER DEFAULT 0,
            last_error VARCHAR(1000)
        );
        CREATE INDEX IF NOT EXISTS ix_task_events_task_id ON task_events(task_id);
        CREATE INDEX IF NOT EXISTS ix_task_events_processing_status ON task_events(processing_status);
        CREATE INDEX IF NOT EXISTS ix_task_events_created_at ON task_events(created_at);
    """)

    # Create audit_logs table
    op.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL,
            action VARCHAR(100) NOT NULL,
            entity_type VARCHAR(50) NOT NULL,
            entity_id UUID NOT NULL,
            details JSONB,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS ix_audit_logs_user_id ON audit_logs(user_id);
        CREATE INDEX IF NOT EXISTS ix_audit_logs_entity_type ON audit_logs(entity_type);
        CREATE INDEX IF NOT EXISTS ix_audit_logs_entity_id ON audit_logs(entity_id);
        CREATE INDEX IF NOT EXISTS ix_audit_logs_created_at ON audit_logs(created_at);
    """)

    # Create notification_deliveries table
    op.execute("""
        CREATE TABLE IF NOT EXISTS notification_deliveries (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES users(id),
            reminder_id UUID REFERENCES task_reminders(id),
            channel notificationchannel NOT NULL,
            recipient VARCHAR(500) NOT NULL,
            subject VARCHAR(200),
            message TEXT NOT NULL,
            status deliverystatus DEFAULT 'pending',
            error_message VARCHAR(500),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            sent_at TIMESTAMP,
            retry_count INTEGER DEFAULT 0,
            next_retry_at TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS ix_notification_deliveries_user_id ON notification_deliveries(user_id);
        CREATE INDEX IF NOT EXISTS ix_notification_deliveries_status ON notification_deliveries(status);
        CREATE INDEX IF NOT EXISTS ix_notification_deliveries_next_retry_at ON notification_deliveries(next_retry_at);
    """)


def downgrade() -> None:
    # Drop tables in reverse order
    op.execute("DROP TABLE IF EXISTS notification_deliveries CASCADE")
    op.execute("DROP TABLE IF EXISTS audit_logs CASCADE")
    op.execute("DROP TABLE IF EXISTS task_events CASCADE")
    op.execute("DROP TABLE IF EXISTS task_tag_associations CASCADE")
    op.execute("DROP TABLE IF EXISTS task_tags CASCADE")
    op.execute("DROP TABLE IF EXISTS task_reminders CASCADE")

    # Drop columns from tasks table
    op.execute("""
        ALTER TABLE tasks
        DROP COLUMN IF EXISTS recurrence_type,
        DROP COLUMN IF EXISTS recurrence_interval,
        DROP COLUMN IF EXISTS next_occurrence_at,
        DROP COLUMN IF EXISTS due_at,
        DROP COLUMN IF EXISTS priority,
        DROP COLUMN IF EXISTS parent_task_id,
        DROP COLUMN IF EXISTS completed_at
    """)

    # Drop enums
    op.execute("DROP TYPE IF EXISTS deliverystatus")
    op.execute("DROP TYPE IF EXISTS notificationchannel")
    op.execute("DROP TYPE IF EXISTS processingstatus")
    op.execute("DROP TYPE IF EXISTS taskeventtype")
    op.execute("DROP TYPE IF EXISTS reminderstatus")
    op.execute("DROP TYPE IF EXISTS priority")
    op.execute("DROP TYPE IF EXISTS recurrencetype")
