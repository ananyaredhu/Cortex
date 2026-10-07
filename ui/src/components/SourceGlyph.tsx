import { BookOpen, HardDrive, Hash, SquareKanban } from "lucide-react";
import type { Source } from "../api/types";

// Generic glyphs, never the vendors' logos (design brief and Cortex readme).
const GLYPH = { confluence: BookOpen, jira: SquareKanban, slack: Hash, gdrive: HardDrive } as const;
export const SOURCE_LABEL: Record<Source, string> = {
  confluence: "Confluence",
  jira: "Jira",
  slack: "Slack",
  gdrive: "Drive",
};

export function SourceGlyph({ source, size = 15 }: { source: Source; size?: number }) {
  const Glyph = GLYPH[source];
  return <Glyph size={size} aria-label={SOURCE_LABEL[source]} />;
}
