import { ListItem } from "@nous-research/ui/ui/components/list-item";
import {
  Check,
  ChevronDown,
  ChevronRight,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
} from "lucide-react";
import { useState } from "react";
import { fetchJSON } from "@/lib/api";

/**
 * Expandable skill-invocation row for the chat sidebar's SKILLS panel —
 * the Echo counterpart to ToolCall. Each row is one skill invocation
 * (echo_skill_invocation) of the current conversation.
 *
 * Two kinds, distinguished by whether the user has rated that exact call:
 *   - rated   → a ✓ on the right; click expands to show the rating
 *               (👍/👎 + reason) plus the Layer A/B/C signals collected.
 *   - unrated → a "rate" affordance; click dispatches `echo:rate-skill`
 *               so the chat:bottom rating bar jumps to this invocation.
 *
 * The rating bar lives in Echo's separate dashboard bundle, so the bridge
 * is a window CustomEvent rather than a shared React tree.
 */

export interface SkillEntry {
  invocation_id: number;
  skill_id: string;
  rated: boolean;
  signal_count: number;
  started_at?: number;
  task_summary?: string;
}

interface SignalEvent {
  event_id: number;
  layer: string;
  signal_type: string;
  value_text?: string | null;
  value_real?: number | null;
  ts: number;
}

interface InvocationDetail {
  rating: { direction: "up" | "down"; reason?: string | null } | null;
  events: SignalEvent[];
}

/** Window event the sidebar fires to ask the chat:bottom rating bar to
 *  target a specific un-rated invocation. Bundle listens for this name. */
export const RATE_SKILL_EVENT = "echo:rate-skill";

export function SkillCall({ skill }: { skill: SkillEntry }) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<InvocationDetail | null>(null);
  const [loading, setLoading] = useState(false);

  const handleClick = () => {
    if (!skill.rated) {
      // Un-rated → ask the bottom rating bar to rate THIS exact call.
      window.dispatchEvent(
        new CustomEvent(RATE_SKILL_EVENT, {
          detail: {
            invocation_id: skill.invocation_id,
            skill_id: skill.skill_id,
          },
        }),
      );
      return;
    }
    const next = !open;
    setOpen(next);
    if (next && !detail && !loading) {
      setLoading(true);
      fetchJSON<InvocationDetail>(
        `/api/plugins/echo_signals/invocations/${skill.invocation_id}/signals`,
      )
        .then((d) => setDetail(d))
        .catch(() => {})
        .finally(() => setLoading(false));
    }
  };

  const Chevron = open ? ChevronDown : ChevronRight;

  return (
    <div
      className={`overflow-hidden rounded-md border ${
        skill.rated
          ? "border-border bg-muted/20"
          : "border-primary/40 bg-primary/[0.04]"
      }`}
    >
      <ListItem
        onClick={handleClick}
        aria-expanded={skill.rated ? open : undefined}
        title={
          skill.rated ? "view rating + signals" : "click to rate this skill"
        }
        className="px-2.5 py-1.5 text-xs hover:bg-foreground/2"
      >
        {skill.rated ? (
          <Chevron className="h-3 w-3 shrink-0 text-muted-foreground" />
        ) : (
          <span className="w-3 shrink-0" />
        )}

        <Sparkles className="h-3 w-3 shrink-0 text-primary/80" />

        <span className="min-w-0 shrink truncate font-mono font-medium">
          {skill.skill_id}
        </span>

        <span className="min-w-0 flex-1 truncate font-mono text-[0.65rem] text-muted-foreground/70">
          {skill.signal_count} signal{skill.signal_count === 1 ? "" : "s"}
        </span>

        {skill.rated ? (
          <Check
            className="h-3 w-3 shrink-0 text-success"
            aria-label="rated"
          />
        ) : (
          <span className="shrink-0 rounded-sm border border-primary/50 px-1 text-[0.6rem] uppercase tracking-wide text-primary">
            rate
          </span>
        )}
      </ListItem>

      {skill.rated && open && (
        <div className="space-y-2 border-t border-border/60 px-3 py-2 text-xs font-mono">
          {loading && <div className="text-muted-foreground">loading…</div>}

          {detail?.rating && (
            <div className="flex items-center gap-2">
              {detail.rating.direction === "up" ? (
                <ThumbsUp className="h-3.5 w-3.5 shrink-0 text-success" />
              ) : (
                <ThumbsDown className="h-3.5 w-3.5 shrink-0 text-destructive" />
              )}
              <span className="text-foreground/90">
                {detail.rating.direction === "up"
                  ? "thumbs up"
                  : "thumbs down"}
              </span>
              {detail.rating.reason && (
                <span className="min-w-0 truncate text-muted-foreground">
                  — {detail.rating.reason}
                </span>
              )}
            </div>
          )}

          {detail && detail.events.length > 0 && (
            <div className="space-y-0.5">
              <div className="text-[0.6rem] uppercase tracking-wider text-muted-foreground/60">
                signals
              </div>
              {detail.events.map((e) => (
                <div
                  key={e.event_id}
                  className="flex items-baseline gap-2 text-muted-foreground"
                >
                  <span className="w-4 shrink-0 text-[0.6rem] uppercase text-muted-foreground/50">
                    {e.layer}
                  </span>
                  <span className="shrink-0 text-foreground/80">
                    {e.signal_type}
                  </span>
                  {e.value_text && (
                    <span className="min-w-0 truncate">{e.value_text}</span>
                  )}
                </div>
              ))}
            </div>
          )}

          {detail && !detail.rating && detail.events.length === 0 && (
            <div className="text-muted-foreground">no signals recorded</div>
          )}
        </div>
      )}
    </div>
  );
}
