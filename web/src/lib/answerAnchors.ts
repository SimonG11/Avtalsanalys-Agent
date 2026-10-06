/**
 * What: works out where in the chat each question's answer card belongs.
 *
 * Why: a question is followed by the agent's messages (its tool calls and their results), and
 * the answer card should come after the last of them, just before the next question. Deciding
 * this from the message list alone keeps the placement independent of how the backend groups
 * its runs (an ask_user interrupt splits one question into two runs).
 *
 * How: walk the messages in order and remember, for every question (user message), the last
 * message before the next question. AnswerCard looks a message up in the resulting map.
 */

export interface MessageLike {
  id: string;
  role: string;
}

/** Maps the id of each question's last message to the id of the question itself. */
export function answerAnchors(messages: readonly MessageLike[]): Map<string, string> {
  const anchors = new Map<string, string>();
  let question: string | null = null;
  messages.forEach((message, index) => {
    if (message.role === "user") question = message.id;
    const next = messages[index + 1];
    if (question !== null && (next === undefined || next.role === "user")) {
      anchors.set(message.id, question);
    }
  });
  return anchors;
}

/** The id of the most recent question, or null when nobody has asked anything yet. */
export function lastQuestionId(messages: readonly MessageLike[]): string | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === "user") return messages[i].id;
  }
  return null;
}
