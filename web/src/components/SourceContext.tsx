"use client";
/**
 * What: lets any part of the chat open a citation in the source panel.
 *
 * Why: the answer card is rendered deep inside the conversation, far from the page layout that
 * owns the source panel. A React context connects the two without passing callbacks through
 * every component in between.
 *
 * How: AgentApp provides `openSource`; AnswerCard calls it when a citation is clicked.
 */
import { createContext, useContext } from "react";

import type { Citation } from "@/lib/contract";

export const OpenSourceContext = createContext<(citation: Citation) => void>(() => {});

export function useOpenSource(): (citation: Citation) => void {
  return useContext(OpenSourceContext);
}
