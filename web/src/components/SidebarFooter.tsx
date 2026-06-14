import { Typography } from "@/components/NouiTypography";
import { useSidebarStatus } from "@/hooks/useSidebarStatus";
import { cn } from "@/lib/utils";

/** Echo's own version (see plugins/echo_signals/plugin.yaml). Shown instead
 *  of the underlying Hermes version — Echo is a distinct project, currently
 *  in alpha. The Hermes version stays available in the hover tooltip. */
const ECHO_VERSION = "0.1.0-alpha";

export function SidebarFooter() {
  const status = useSidebarStatus();

  return (
    <div
      className={cn(
        "flex shrink-0 items-center justify-between gap-2",
        "px-5 py-2.5",
        "border-t border-current/10",
      )}
    >
      <Typography
        mondwest
        className="font-mono-ui text-[0.7rem] tabular-nums tracking-[0.1em] text-muted-foreground/70 lowercase"
        title={
          status?.version != null
            ? `Echo ${ECHO_VERSION} · on Hermes ${status.version}`
            : `Echo ${ECHO_VERSION}`
        }
      >
        {`v${ECHO_VERSION}`}
      </Typography>

      <a
        href="https://github.com/Prt7-26"
        target="_blank"
        rel="noopener noreferrer"
        className={cn(
          "font-mondwest text-[0.65rem] tracking-[0.15em] text-midground",
          "transition-opacity hover:opacity-90",
          "focus-visible:rounded-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-midground/40",
        )}
        style={{ mixBlendMode: "var(--ui-chrome-blend, plus-lighter)" } as unknown as React.CSSProperties}
      >
        Prt7-26
      </a>
    </div>
  );
}
