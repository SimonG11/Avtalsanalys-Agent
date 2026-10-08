"use client";
/**
 * What: the whole page in the browser: a header, the conversation, and the source panel beside
 * it when a citation is open.
 *
 * Why: the conversation and the source panel follow the same agent run, so they live under one
 * CopilotKit provider that holds the connection to the agent.
 *
 * How: CopilotKitProvider talks to the CopilotKit runtime at /api/copilotkit, which forwards
 * runs to the backend's AG-UI endpoint. Only CopilotKit's hooks are used, not its chat
 * components, so the app's look is entirely its own (globals.css). AnswersProvider keeps the
 * answers; the open citation is React state here and reaches the answers through
 * OpenSourceContext. A new conversation reloads the page, which gives the agent a new thread
 * and leaves nothing of the previous one behind.
 */
import { CopilotKitProvider, UseAgentUpdate, useAgent } from "@copilotkit/react-core/v2";
import { useState } from "react";

import { AGENT_ID } from "@/lib/agent";
import type { Citation } from "@/lib/contract";

import { AnswersProvider } from "./Answers";
import { Conversation } from "./Conversation";
import { Icon } from "./icons";
import { SourcePanel } from "./SourcePanel";
import { OpenSourceContext } from "./SourceContext";
import { ThemeToggle } from "./ThemeToggle";
import styles from "./AgentApp.module.css";

export function AgentApp() {
  const [source, setSource] = useState<Citation | null>(null);

  return (
    <CopilotKitProvider
      runtimeUrl="/api/copilotkit"
      agentId={AGENT_ID}
      enableInspector={false}
      showIntelligenceIndicator={false}
    >
      <AnswersProvider>
        <OpenSourceContext.Provider value={setSource}>
          <div className={styles.app}>
            <Header />
            <main className={source ? `${styles.main} ${styles.withSource}` : styles.main}>
              <section className={styles.chat} aria-label="Konversation">
                <Conversation />
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
        </OpenSourceContext.Provider>
      </AnswersProvider>
    </CopilotKitProvider>
  );
}

const MESSAGE_UPDATES = [UseAgentUpdate.OnMessagesChanged];

function Header() {
  const { agent } = useAgent({ agentId: AGENT_ID, updates: MESSAGE_UPDATES });
  const started = agent.messages.length > 0;
  return (
    <header className={styles.header}>
      <div className={styles.brand}>
        <span className={styles.logo} aria-hidden="true">
          <Icon name="document" size={16} />
        </span>
        <h1 className={styles.name}>Avtalsanalys</h1>
        <span className={styles.tagline}>Statens inköpscentrals ramavtal, med källor</span>
      </div>
      <div className={styles.tools}>
        {started && (
          <button
            type="button"
            className={styles.textButton}
            onClick={() => window.location.reload()}
          >
            <Icon name="plus" size={16} />
            Ny konversation
          </button>
        )}
        <ThemeToggle />
      </div>
    </header>
  );
}
