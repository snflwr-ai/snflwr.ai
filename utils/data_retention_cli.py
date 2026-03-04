#!/usr/bin/env python3
# utils/data_retention_cli.py
"""
Data Retention Management CLI
Command-line utility for managing COPPA-compliant data retention
"""

import argparse
import sys
import json
from datetime import datetime, timezone
from typing import Dict

from utils.data_retention import data_retention_manager
from config import safety_config
from utils.logger import get_logger

logger = get_logger(__name__)


class DataRetentionCLI:
    """Command-line interface for data retention management"""

    def __init__(self):
        """Initialize CLI"""
        self.manager = data_retention_manager
        self.parser = self._create_parser()

    def _create_parser(self) -> argparse.ArgumentParser:
        """Create argument parser"""
        parser = argparse.ArgumentParser(
            description='snflwr.ai Data Retention Management',
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="""
Examples:
  # Show retention status
  python -m utils.data_retention_cli status

  # Run cleanup now
  python -m utils.data_retention_cli cleanup

  # Show retention policy
  python -m utils.data_retention_cli policy

  # Export retention summary
  python -m utils.data_retention_cli export --output retention_report.json
            """
        )

        subparsers = parser.add_subparsers(dest='command', help='Command to execute')

        # Status command
        subparsers.add_parser(
            'status',
            help='Show current data retention status and volumes'
        )

        # Policy command
        subparsers.add_parser(
            'policy',
            help='Display data retention policy details'
        )

        # Cleanup command
        cleanup_parser = subparsers.add_parser(
            'cleanup',
            help='Run data retention cleanup now'
        )
        cleanup_parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without actually deleting'
        )

        # Export command
        export_parser = subparsers.add_parser(
            'export',
            help='Export retention summary to JSON file'
        )
        export_parser.add_argument(
            '--output',
            type=str,
            default='retention_summary.json',
            help='Output file path (default: retention_summary.json)'
        )

        # Stats command
        subparsers.add_parser(
            'stats',
            help='Show detailed statistics for each data category'
        )

        return parser

    def run(self, args=None):
        """Run CLI with provided arguments"""
        parsed_args = self.parser.parse_args(args)

        if not parsed_args.command:
            self.parser.print_help()
            return 1

        # Execute command
        commands = {
            'status': self.cmd_status,
            'policy': self.cmd_policy,
            'cleanup': self.cmd_cleanup,
            'export': self.cmd_export,
            'stats': self.cmd_stats
        }

        try:
            return commands[parsed_args.command](parsed_args)
        except Exception as e:
            logger.error(f"Command failed: {e}")
            print(f"❌ Error: {e}", file=sys.stderr)
            return 1

    def cmd_status(self, args) -> int:
        """Show current data retention status"""
        print("=" * 70)
        print("DATA RETENTION STATUS")
        print("=" * 70)
        print()

        try:
            summary = self.manager.get_retention_summary()

            # Configuration status
            print("📋 Configuration:")
            print(f"   Cleanup Enabled: {'✅ Yes' if summary['cleanup_enabled'] else '❌ No'}")
            print(f"   Cleanup Schedule: {summary['cleanup_schedule']}")
            print()

            # Data volumes
            print("📊 Current Data Volumes:")
            print()
            for table_info in summary['data_volumes']:
                table_name = table_info['table']
                retention_days = table_info['retention_days']
                total = table_info['total_records']

                print(f"   {table_name}:")
                print(f"      Total Records: {total:,}")
                print(f"      Retention Period: {retention_days} days")

                # Additional info for specific tables
                if 'resolved_records' in table_info:
                    resolved = table_info['resolved_records']
                    print(f"      Resolved Records: {resolved:,}")
                if 'ended_sessions' in table_info:
                    ended = table_info['ended_sessions']
                    print(f"      Ended Sessions: {ended:,}")

                print()

            print("=" * 70)
            return 0

        except Exception as e:
            logger.error(f"Failed to get status: {e}")
            print(f"❌ Failed to retrieve status: {e}", file=sys.stderr)
            return 1

    def cmd_policy(self, args) -> int:
        """Display data retention policy"""
        print("=" * 70)
        print("DATA RETENTION POLICY")
        print("=" * 70)
        print()

        try:
            policy = safety_config.get_retention_policy()

            # Compliance framework
            compliance = policy.get('compliance', {})
            print("🔒 Compliance Framework:")
            print(f"   Standard: {compliance.get('framework', 'N/A')}")
            print(f"   Data Minimization: {'✅ Enabled' if compliance.get('data_minimization') else '❌ Disabled'}")
            print(f"   Automatic Cleanup: {'✅ Enabled' if compliance.get('automatic_cleanup') else '❌ Disabled'}")
            print(f"   Parent Controls: {'✅ Enabled' if compliance.get('parent_controls') else '❌ Disabled'}")
            print()

            # Retention periods by category
            print("📅 Retention Periods:")
            print()

            for category, details in policy.items():
                if category == 'compliance':
                    continue

                if isinstance(details, dict):
                    retention_days = details.get('retention_days', 'N/A')
                    description = details.get('description', '')
                else:
                    retention_days = details
                    description = ''

                print(f"   {category.replace('_', ' ').title()}:")
                print(f"      Retention: {retention_days} days")
                print(f"      Policy: {description}")
                print()

            print("=" * 70)
            print()
            print("ℹ️  Note: Resolved incidents are automatically deleted after retention period")
            print("ℹ️  Parents can export data before deletion through the dashboard")
            print()

            return 0

        except Exception as e:
            logger.error(f"Failed to get policy: {e}")
            print(f"❌ Failed to retrieve policy: {e}", file=sys.stderr)
            return 1

    def cmd_cleanup(self, args) -> int:
        """Run data retention cleanup"""
        if args.dry_run:
            print("🔍 DRY RUN MODE - No data will be deleted")
            print()

        print("=" * 70)
        print("DATA RETENTION CLEANUP")
        print("=" * 70)
        print()
        print(f"Started: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}")
        print()

        if not safety_config.DATA_CLEANUP_ENABLED:
            print("⚠️  Warning: Data cleanup is disabled in configuration")
            if not args.dry_run:
                print("   Cleanup will not run. Enable DATA_CLEANUP_ENABLED in config.py")
                return 1

        try:
            if args.dry_run:
                return self._dry_run_cleanup()

            # Run cleanup
            results = self.manager.run_all_cleanup_tasks()

            # Display results
            print("📋 Cleanup Results:")
            print()

            total_deleted = 0
            for task_name, task_result in results['tasks'].items():
                status = task_result.get('status', 'unknown')
                status_icon = '✅' if status == 'success' else '❌'

                print(f"   {status_icon} {task_name}:")

                if status == 'success':
                    if 'deleted_count' in task_result:
                        deleted = task_result['deleted_count']
                        total_deleted += deleted
                        print(f"      Deleted: {deleted:,} records")
                    else:
                        print(f"      Status: Completed")
                else:
                    error = task_result.get('error', 'Unknown error')
                    print(f"      Error: {error}")

                print()

            print("=" * 70)
            print(f"Total Records Deleted: {total_deleted:,}")
            print(f"Completed: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}")
            print()

            return 0

        except Exception as e:
            logger.error(f"Cleanup failed: {e}")
            print(f"❌ Cleanup failed: {e}", file=sys.stderr)
            return 1

    def _dry_run_cleanup(self) -> int:
        """Show what cleanup would delete without deleting anything"""
        from datetime import timedelta
        from storage.database import db_manager
        from storage.db_adapters import DB_ERRORS

        categories = [
            {
                'name': 'Safety Incidents (resolved)',
                'query': "SELECT COUNT(*) as count FROM safety_incidents WHERE resolved = 1 AND resolved_at < ?",
                'retention_days': safety_config.SAFETY_LOG_RETENTION_DAYS,
            },
            {
                'name': 'Audit Logs',
                'query': "SELECT COUNT(*) as count FROM audit_log WHERE timestamp < ?",
                'retention_days': safety_config.AUDIT_LOG_RETENTION_DAYS,
            },
            {
                'name': 'Sessions (ended)',
                'query': "SELECT COUNT(*) as count FROM sessions WHERE ended_at IS NOT NULL AND ended_at < ?",
                'retention_days': safety_config.SESSION_RETENTION_DAYS,
            },
            {
                'name': 'Conversations',
                'query': "SELECT COUNT(*) as count FROM conversations WHERE updated_at < ?",
                'retention_days': safety_config.CONVERSATION_RETENTION_DAYS,
            },
            {
                'name': 'Learning Analytics',
                'query': "SELECT COUNT(*) as count FROM learning_analytics WHERE date < ?",
                'retention_days': safety_config.ANALYTICS_RETENTION_DAYS,
            },
            {
                'name': 'Auth Tokens (expired/invalid)',
                'query': "SELECT COUNT(*) as count FROM auth_tokens WHERE expires_at < ? OR is_valid = 0",
                'retention_days': 0,  # uses current time as cutoff
            },
        ]

        print("📋 Records that would be deleted:")
        print()

        total = 0
        for cat in categories:
            if cat['retention_days'] > 0:
                cutoff = (datetime.now(timezone.utc) - timedelta(days=cat['retention_days'])).isoformat()
            else:
                cutoff = datetime.now(timezone.utc).isoformat()

            try:
                result = db_manager.execute_query(cat['query'], (cutoff,))
                count = result[0]['count'] if result else 0
            except DB_ERRORS:
                count = 0

            total += count
            icon = '🗑️ ' if count > 0 else '   '
            retention_info = f"(>{cat['retention_days']}d old)" if cat['retention_days'] > 0 else "(expired)"
            print(f"   {icon}{cat['name']}: {count:,} records {retention_info}")

        print()
        print(f"   Total: {total:,} records would be deleted")
        print()
        print("ℹ️  Run without --dry-run to execute cleanup")
        return 0

    def cmd_export(self, args) -> int:
        """Export retention summary to JSON"""
        print(f"📤 Exporting retention summary to {args.output}...")
        print()

        try:
            # Get comprehensive summary
            summary = self.manager.get_retention_summary()
            policy = safety_config.get_retention_policy()

            export_data = {
                'generated_at': datetime.now(timezone.utc).isoformat(),
                'application': 'snflwr.ai',
                'retention_summary': summary,
                'retention_policy': policy
            }

            # Write to file
            with open(args.output, 'w') as f:
                json.dump(export_data, f, indent=2)

            print(f"✅ Successfully exported to {args.output}")
            print()
            print(f"   File size: {len(json.dumps(export_data))} bytes")
            print()

            return 0

        except Exception as e:
            logger.error(f"Export failed: {e}")
            print(f"❌ Export failed: {e}", file=sys.stderr)
            return 1

    def cmd_stats(self, args) -> int:
        """Show detailed statistics"""
        print("=" * 70)
        print("DETAILED DATA STATISTICS")
        print("=" * 70)
        print()

        try:
            summary = self.manager.get_retention_summary()

            for table_info in summary['data_volumes']:
                table_name = table_info['table']
                retention_days = table_info['retention_days']
                total = table_info['total_records']

                print(f"📊 {table_name.replace('_', ' ').title()}")
                print(f"   {'─' * 60}")
                print(f"   Total Records: {total:,}")
                print(f"   Retention Period: {retention_days} days")

                # Calculate estimated deletion date for oldest records
                if total > 0:
                    print(f"   Next Cleanup: Daily at {safety_config.DATA_CLEANUP_HOUR:02d}:00")

                # Additional metrics
                if 'resolved_records' in table_info:
                    resolved = table_info['resolved_records']
                    unresolved = total - resolved
                    print(f"   Resolved: {resolved:,}")
                    print(f"   Unresolved: {unresolved:,}")

                if 'ended_sessions' in table_info:
                    ended = table_info['ended_sessions']
                    active = total - ended
                    print(f"   Ended Sessions: {ended:,}")
                    print(f"   Active Sessions: {active:,}")

                print()

            # Compliance summary
            print("🔒 Compliance Status:")
            print(f"   {'─' * 60}")
            print(f"   Framework: COPPA/FERPA")
            print(f"   Data Minimization: ✅ Active")
            print(f"   Automatic Cleanup: {'✅ Enabled' if safety_config.DATA_CLEANUP_ENABLED else '❌ Disabled'}")
            print(f"   Audit Logging: {'✅ Enabled' if safety_config.ENABLE_AUDIT_LOGGING else '❌ Disabled'}")
            print()

            print("=" * 70)
            return 0

        except Exception as e:
            logger.error(f"Failed to get statistics: {e}")
            print(f"❌ Failed to retrieve statistics: {e}", file=sys.stderr)
            return 1


def main():
    """Main entry point"""
    cli = DataRetentionCLI()
    sys.exit(cli.run())


if __name__ == '__main__':
    main()
