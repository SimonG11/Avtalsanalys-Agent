"use client";
/**
 * What: the chat: questions, the agent's steps and its answers, with example questions before
 * the first message.
 *
 * Why: CopilotKit's CopilotChat already handles the AG-UI stream, message list, input and
 * scrolling. This component only adds what is ours: Swedish texts, the step renderer and the
 * example questions.
 *
 * How: `useRenderTool` with the name "*" makes AgentStep render every tool call except the one
 * that hands in the answer (FinalAnswer, see lib/tools.ts); the answer
 * card is registered on the provider (see AgentApp). Labels CopilotKit shows are translated here.
 */
import { CopilotChat, useConfigureSuggestions, useRenderTool } from "@copilotkit/react-core/v2";

import { AGENT_ID } from "@/lib/agent";
import { isHiddenTool } from "@/lib/tools";

import { AgentStep } from "./AgentSteps";
import type { AgentStepProps } from "./AgentSteps";

const LABELS = {
  chatInputPlaceholder: "Ställ en fråga om ramavtalen…",
  chatInputToolbarAddButtonLabel: "Bifoga",
  chatInputToolbarToolsButtonLabel: "Verktyg",
  chatInputToolbarStartTranscribeButtonLabel: "Diktera",
  chatInputToolbarCancelTranscribeButtonLabel: "Avbryt",
  chatInputToolbarFinishTranscribeButtonLabel: "Klar",
  assistantMessageToolbarCopyCodeLabel: "Kopiera",
  assistantMessageToolbarCopyCodeCopiedLabel: "Kopierat",
  assistantMessageToolbarCopyMessageLabel: "Kopiera",
  assistantMessageToolbarThumbsUpLabel: "Bra svar",
  assistantMessageToolbarThumbsDownLabel: "Dåligt svar",
  assistantMessageToolbarReadAloudLabel: "Läs upp",
  assistantMessageToolbarRegenerateLabel: "Svara igen",
  userMessageToolbarCopyMessageLabel: "Kopiera",
  userMessageToolbarEditMessageLabel: "Redigera",
  chatDisclaimerText: "Svaren bygger på avtalstexten. Öppna källan innan du agerar på ett svar.",
  modalHeaderTitle: "Avtalsanalys",
  welcomeMessageText:
    "Fråga om Statens inköpscentrals ramavtal. Agenten söker själv i avtalen och varje svar har källor du kan öppna.",
};

const EXAMPLE_QUESTIONS = [
  { title: "Uppsägningstid", message: "Vilken uppsägningstid gäller för ett kontrakt?" },
  { title: "Uppsägning i IT-drift", message: "Hur säger kunden upp ett kontrakt inom IT-drift?" },
  { title: "Vite", message: "Vilket vite gäller vid försenad leverans?" },
];

export function Chat() {
  useRenderTool(
    {
      name: "*",
      agentId: AGENT_ID,
      render: (props: AgentStepProps) =>
        isHiddenTool(props.name) ? null : <AgentStep {...props} />,
    },
    [],
  );
  useConfigureSuggestions(
    {
      suggestions: EXAMPLE_QUESTIONS,
      available: "before-first-message",
      consumerAgentId: AGENT_ID,
    },
    [],
  );

  return <CopilotChat agentId={AGENT_ID} labels={LABELS} input={{ addMenuButton: NoAddMenu }} />;
}

/** The chat takes no attachments, so the input's "+" menu is left out. */
function NoAddMenu() {
  return null;
}
