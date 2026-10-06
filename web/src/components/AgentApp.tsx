"use client";
/**
 * What: the whole page in the browser: the chat on the left, the source panel on the right
 * when a citation is open, and the dialog when the agent asks something.
 *
 * Why: all three parts follow the same agent run, so they live under one CopilotKit provider
 * that holds the connection to the agent.
 *
 * How: CopilotKitProvider talks to the CopilotKit runtime at /api/copilotkit, which forwards
 * runs to the backend's AG-UI endpoint. AnswersProvider keeps the answers; the chosen citation
 * is React state here and reaches the answer card through OpenSourceContext.
 */
import { CopilotKitProvider } from "@copilotkit/react-core/v2";
import { useState } from "react";

import { AGENT_ID } from "@/lib/agent";
import type { Citation } from "@/lib/contract";

import { AnswersProvider, answerRenderers } from "./Answers";
import { Chat } from "./Chat";
import { ClarifyDialog } from "./ClarifyDialog";
import { SourcePanel } from "./SourcePanel";
import { OpenSourceContext } from "./SourceContext";
import styles from "./AgentApp.module.css";

export function AgentApp() {
  const [source, setSource] = useState<Citation | null>(null);

  return (
    <CopilotKitProvider
      runtimeUrl="/api/copilotkit"
      agentId={AGENT_ID}
      renderCustomMessages={answerRenderers}
      enableInspector={false}
      showIntelligenceIndicator={false}
    >
      <AnswersProvider>
        <OpenSourceContext.Provider value={setSource}>
          <div className={styles.app}>
            <header className={styles.header}>
              <h1 className={styles.brand}>Avtalsanalys</h1>
              <p className={styles.tagline}>Frågor om ramavtal, med källor ur avtalstexten</p>
            </header>
            <main className={source ? `${styles.main} ${styles.withSource}` : styles.main}>
              <section className={styles.chat} aria-label="Chatt">
                <Chat />
              </section>
              {source && (
                <SourcePanel
                  key={`${source.sha256}:${source.page}:${source.quote}`}
                  citation={source}
                  onClose={() => setSource(null)}
                />
              )}
            </main>
          </div>
          <ClarifyDialog />
        </OpenSourceContext.Provider>
      </AnswersProvider>
    </CopilotKitProvider>
  );
}
