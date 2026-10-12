import { AlertTriangle, CalendarDays, ClipboardList, Gavel, type LucideIcon, MessagesSquare, Siren, Sun, Users } from "lucide-react";
import type { RoutineSchedule, Source } from "../api/types";

/**
 * Starting points for a routine. A template only fills the form: the person can change every field before saving, and
 * what is saved is an ordinary routine of theirs. Each instruction asks Cortex to read and report, never to act: a run
 * is an ordinary question and the model has no write tools (docs/routines.md, "Out").
 *
 * Every instruction here was run against the real Brain on 12 Oct and answered. The first four say "my tickets", "my brief",
 * "my stand-up", "my week" or "my status update" on purpose: those words select the Brain's `my-work` skill, which finds the
 * person's tickets by assignee (brain/skills/my-work). Plain search cannot: no ticket's text says "my". Two earlier
 * templates were dropped because nothing can answer them yet: "pages that changed in the last week" (no search by date) and
 * "threads I have not replied to" (no lookup of who was asked).
 */
export interface RoutineTemplate {
  id: string;
  icon: LucideIcon;
  name: string;
  description: string;
  instructions: string;
  sources: Source[]; // empty: all sources
  schedule: RoutineSchedule;
}

const weekdays = (time: string): RoutineSchedule => ({ frequency: "weekdays", weekday: null, time });
const weekly = (weekday: number, time: string): RoutineSchedule => ({ frequency: "weekly", weekday, time }); // Monday = 0

export const TEMPLATES: RoutineTemplate[] = [
  {
    id: "morning-brief",
    icon: Sun,
    name: "Morning brief",
    description: "Your open tickets, what changed overnight, and who is waiting on you.",
    instructions: [
      "Prepare my brief for today.",
      "1. List my open tickets, most urgent first, with the status and the due date where one is given.",
      "2. Say what changed on them since the last working day.",
      "3. List threads and pages where someone is waiting on me.",
      "End with the three things I should do first.",
    ].join("\n"),
    sources: [],
    schedule: weekdays("09:00"),
  },
  {
    id: "standup-prep",
    icon: Users,
    name: "Stand-up prep",
    description: "A draft of your stand-up update: done, doing, blocked.",
    instructions: [
      "Draft my stand-up update in three parts.",
      "1. Done: what I moved forward since the last working day.",
      "2. Doing: what I am working on today.",
      "3. Blocked: anything waiting on someone else, and on whom.",
      "Use my tickets and the threads I took part in. Keep it under 120 words.",
    ].join("\n"),
    sources: ["jira", "slack"],
    schedule: weekdays("09:30"),
  },
  {
    id: "week-ahead",
    icon: CalendarDays,
    name: "Week ahead",
    description: "What is due this week and what is at risk of slipping.",
    instructions: [
      "Plan my week.",
      "1. List my tickets due this week, in date order.",
      "2. Mark any that have not moved in the last five working days.",
      "3. List tickets assigned to me that have no due date.",
      "Say which one is most at risk and why.",
    ].join("\n"),
    sources: ["jira"],
    schedule: weekly(0, "09:00"),
  },
  {
    id: "weekly-wrap-up",
    icon: ClipboardList,
    name: "Weekly wrap-up",
    description: "A status update for the week, ready to edit and share.",
    instructions: [
      "Write my status update for this week.",
      "1. Shipped: tickets I closed and pages I published.",
      "2. In progress: what moved and what is left.",
      "3. Carried over: what did not get done, with the reason where one is recorded.",
      "Use short bullets, and cite the ticket or page for each.",
    ].join("\n"),
    sources: [],
    schedule: weekly(4, "16:00"),
  },
  {
    id: "blocker-watch",
    icon: AlertTriangle,
    name: "Blocker watch",
    description: "Blocked tickets and open risks in your projects.",
    instructions: [
      "Check my projects for blockers.",
      "1. List tickets marked blocked or waiting, and what each is waiting for.",
      "2. List problems raised in threads today that have no ticket yet.",
      "If there is nothing new since yesterday, say so in one line.",
    ].join("\n"),
    sources: ["jira", "slack"],
    schedule: weekdays("14:00"),
  },
  {
    id: "decision-log",
    icon: Gavel,
    name: "Decision log",
    description: "Decisions made this week, with who made them and where.",
    instructions: [
      "List the decisions made this week in my projects.",
      "For each one give the decision in one sentence, who made it, the date, and the thread or page that records it.",
      "Leave out anything still being discussed, and list those separately as open questions.",
    ].join("\n"),
    sources: ["slack", "confluence"],
    schedule: weekly(4, "15:00"),
  },
  {
    id: "incident-follow-ups",
    icon: Siren,
    name: "Incident follow-ups",
    description: "Recent incidents, their root causes, and which follow-ups are still open.",
    instructions: [
      "Review the incidents and outages in my projects.",
      "1. For each incident give what happened, the root cause where one is stated, and the date.",
      "2. List the follow-up tickets with their status and owner.",
      "3. Say which follow-ups are still open.",
    ].join("\n"),
    sources: [],
    schedule: weekly(0, "10:00"),
  },
  {
    id: "design-discussions",
    icon: MessagesSquare,
    name: "Design discussions",
    description: "What was debated, what was decided, and what is still open.",
    instructions: [
      "Summarise the design discussions in my projects.",
      "1. For each topic give the options that were raised and who raised them.",
      "2. Say what was decided, and link the decision document where there is one.",
      "3. List the questions that are still open.",
    ].join("\n"),
    sources: ["slack", "confluence"],
    schedule: weekly(3, "16:00"),
  },
];
