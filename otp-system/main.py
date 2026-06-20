# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# Main Orchestrator - System Startup & Management
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
#
# This module orchestrates the startup, coordination, and graceful shutdown of all
# microservices in the OTP processing system. It acts as the system supervisor,
# ensuring all components are ready and operational before accepting traffic.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════

import asyncio
import logging
import signal
import sys
from typing import List, Optional
from datetime import datetime

from shared.config_manager import initialize_config, get_config
from services.resilience_manager import ResilienceManager
from services.processor_service import ProcessorService
from services.intelligence_engine import IntelligenceEngine


logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# SYSTEM STATE MANAGER
# ─────────────────────────────────────────────────────────────────────────────────────────────────

class SystemStateManager:
    """
    Manages overall system state and coordinates startup/shutdown of all services.
    """
    
    def __init__(self):
        """Initialize system state."""
        self.config = get_config()
        self.running = False
        self.startup_time: Optional[datetime] = None
        self.services: dict = {}
        self.shutdown_event = asyncio.Event()
    
    async def startup(self):
        """Startup all system components in dependency order."""
        logger.info("╔════════════════════════════════════════════════════════════════╗")
        logger.info("║     OTP Processing System - High-Availability Edition           ║")
        logger.info(f"║     Version: {self.config.get('system.version', '1.0.0')}                                               ║")
        logger.info(f"║     Environment: {self.config.get('system.environment', 'production')}                                  ║")
        logger.info("╚════════════════════════════════════════════════════════════════╝")
        
        try:
            # Step 1: Log configuration summary
            self.config.log_config_summary()
            
            # Step 2: Initialize Resilience Manager (stateful, must be first)
            logger.info("[1/4] Initializing Resilience Manager...")
            self.services['resilience_manager'] = ResilienceManager(
                self.config.get_dict('resilience')
            )
            await self.services['resilience_manager'].start()
            logger.info("✓ Resilience Manager ready")
            
            # Step 3: Initialize Intelligence Engine
            logger.info("[2/4] Initializing Intelligence Engine...")
            intelligence_config = self.config.get_dict('intelligence')
            self.services['intelligence_engine'] = IntelligenceEngine(intelligence_config)
            logger.info("✓ Intelligence Engine ready")
            
            # Step 4: Initialize Processor Service
            logger.info("[3/4] Initializing Processor Service...")
            processor_config = self.config.get_dict('pipeline')
            self.services['processor_service'] = ProcessorService(processor_config)
            logger.info("✓ Processor Service ready")
            
            # Step 5: Health check all services
            logger.info("[4/4] Performing system health checks...")
            health_status = await self._perform_health_checks()
            
            if health_status['all_healthy']:
                logger.info("✓ All services healthy - System ready for traffic")
            else:
                logger.warning("⚠ Some services degraded - System partially ready")
                logger.warning(f"  Degraded services: {health_status['degraded_services']}")
            
            self.running = True
            self.startup_time = datetime.utcnow()
            
            logger.info(f"System startup completed in {self._get_uptime_seconds():.2f} seconds")
            
        except Exception as e:
            logger.error(f"System startup failed: {e}", exc_info=True)
            await self.shutdown()
            raise
    
    async def _perform_health_checks(self) -> dict:
        """
        Perform comprehensive health checks on all services.
        """
        health_checks = {
            'processor': await self.services['processor_service'].health_check(),
            'resilience': self.services['resilience_manager'].get_system_health(),
        }
        
        all_healthy = all(check.get('status') == 'healthy' for check in health_checks.values())
        degraded = [name for name, check in health_checks.items() if check.get('status') != 'healthy']
        
        return {
            'all_healthy': all_healthy,
            'checks': health_checks,
            'degraded_services': degraded,
        }
    
    async def shutdown(self):
        """Graceful shutdown of all services."""
        if not self.running:
            return
        
        logger.info("╔════════════════════════════════════════════════════════════════╗")
        logger.info("║     System Shutdown Initiated - Graceful Termination            ║")
        logger.info("╚════════════════════════════════════════════════════════════════╝")
        
        self.running = False
        
        try:
            # Shutdown services in reverse order
            if 'processor_service' in self.services:
                logger.info("Shutting down Processor Service...")
                await self.services['processor_service'].shutdown()
                logger.info("✓ Processor Service stopped")
            
            if 'intelligence_engine' in self.services:
                logger.info("Shutting down Intelligence Engine...")
                # Intelligence Engine is stateless, so just clear references
                logger.info("✓ Intelligence Engine stopped")
            
            if 'resilience_manager' in self.services:
                logger.info("Shutting down Resilience Manager...")
                await self.services['resilience_manager'].stop()
                logger.info("✓ Resilience Manager stopped")
            
            logger.info("System shutdown completed successfully")
            
        except Exception as e:
            logger.error(f"Error during shutdown: {e}", exc_info=True)
    
    def get_uptime(self) -> float:
        """Get system uptime in seconds."""
        if self.startup_time:
            return (datetime.utcnow() - self.startup_time).total_seconds()
        return 0.0
    
    def _get_uptime_seconds(self) -> float:
        """Helper to get uptime."""
        return self.get_uptime()
    
    def get_status(self) -> dict:
        """Get overall system status."""
        return {
            'status': 'running' if self.running else 'stopped',
            'uptime_seconds': self.get_uptime(),
            'startup_time': self.startup_time.isoformat() if self.startup_time else None,
            'services': {
                name: {'status': 'ready'} for name in self.services.keys()
            }
        }


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# SIGNAL HANDLERS (Graceful Shutdown)
# ─────────────────────────────────────────────────────────────────────────────────────────────────

def setup_signal_handlers(system: SystemStateManager):
    """Setup OS signal handlers for graceful shutdown."""
    
    def handle_signal(signum, frame):
        """Handle SIGTERM/SIGINT for graceful shutdown."""
        signal_name = {signal.SIGTERM: 'SIGTERM', signal.SIGINT: 'SIGINT'}.get(signum, 'UNKNOWN')
        logger.info(f"Received {signal_name}, initiating graceful shutdown...")
        
        # Trigger shutdown
        system.shutdown_event.set()
    
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    logger.info("Signal handlers registered (SIGTERM, SIGINT)")


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────────────────────────

async def main():
    """Main entry point for the OTP system."""
    
    # Initialize configuration
    config_path = None  # Auto-detect from standard locations
    initialize_config(config_path)
    
    # Create system state manager
    system = SystemStateManager()
    
    # Setup signal handlers
    setup_signal_handlers(system)
    
    try:
        # Startup
        await system.startup()
        
        # Keep system running until shutdown signal
        logger.info("System running. Waiting for shutdown signal...")
        await system.shutdown_event.wait()
        
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
    finally:
        # Graceful shutdown
        await system.shutdown()
        logger.info("System shutdown complete")
        sys.exit(0)


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# DEVELOPMENT MODE (For local testing)
# ─────────────────────────────────────────────────────────────────────────────────────────────────

async def run_integration_test():
    """
    Run a simple integration test to verify system components.
    Useful for development and debugging.
    """
    logger.info("Running integration test...")
    
    # Initialize configuration
    initialize_config()
    config = get_config()
    
    # Create a test message
    from shared.models import RawMessage, MessageSourceType
    import uuid
    
    test_message = RawMessage(
        message_id=str(uuid.uuid4()),
        source_type=MessageSourceType.EMAIL,
        source_provider="test-provider",
        subject="Test OTP",
        body="Your verification code is 123456. Do not share.",
    )
    
    logger.info(f"Created test message: {test_message.message_id}")
    
    # Test Intelligence Engine
    logger.info("Testing Intelligence Engine...")
    intelligence = IntelligenceEngine(config.get_dict('intelligence'))
    result = await intelligence.process(test_message)
    
    logger.info(f"Extracted OTPs: {[e.code for e in result.extractions]}")
    logger.info(f"Top extraction: {result.top_extraction}")
    logger.info(f"Quality score: {result.quality_score:.2f}")
    
    # Test Processor
    logger.info("Testing Processor Service...")
    processor = ProcessorService(config.get_dict('pipeline'))
    processing_result = await processor.process(result, test_message)
    
    logger.info(f"Processing result accepted: {processing_result.accepted}")
    logger.info(f"Primary OTP: {processing_result.primary_otp}")
    logger.info(f"Processing latency: {processing_result.total_latency_ms:.2f}ms")
    
    # Cleanup
    await processor.shutdown()
    
    logger.info("Integration test completed successfully!")


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# CLI ENTRY POINTS
# ─────────────────────────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="OTP Processing System")
    parser.add_argument('--config', type=str, help='Path to config.yaml')
    parser.add_argument('--test', action='store_true', help='Run integration test')
    parser.add_argument('--log-level', type=str, default='INFO', help='Logging level')
    
    args = parser.parse_args()
    
    # Run appropriate mode
    if args.test:
        logger.info("Running in TEST mode")
        asyncio.run(run_integration_test())
    else:
        logger.info("Running in PRODUCTION mode")
        asyncio.run(main())
