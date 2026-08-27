from zapv2 import ZAPv2

class OwaspZapConfigurator:
    @staticmethod
    def config_zap(
        zap: ZAPv2
    ):
        """Reset ZAP state and apply default scan settings for standard (non-interactive) scans."""
        zap.core.set_mode(mode="standard")
        zap.core.delete_all_alerts()
        zap.core.new_session("", True)
        zap.pscan.enable_all_scanners()
        zap.pscan.enable_all_tags()
        zap.ascan.enable_all_scanners()
        zap.spider.set_option_max_children(10)
        zap.spider.set_option_max_duration(5)
        zap.spider.set_option_max_depth(5)
        zap.spider.set_option_thread_count(5)
        zap.spider.set_option_parse_comments(False)
        zap.ascan.set_option_max_scan_duration_in_mins(0)
        zap.ascan.set_option_max_rule_duration_in_mins(0)
        zap.ascan.set_option_thread_per_host(2)

        # Maybe will add later when the tool run completely ok due to still in development state
        # Apply threshold overrides after scanners are enabled (order matters).
        # OwaspZapUtils.apply_pscan_threshold_overrides(zap, threshold_overrides)
