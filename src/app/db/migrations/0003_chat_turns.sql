-- One row per answered chat turn: who owns the trace (feedback may only rate the caller's own
-- answers, QA-024) and which sources the answer listed, so a conversation reopened from the
-- history shows them again (QA-041). Deleting the conversation (LGPD, retention) removes them.
CREATE TABLE chat_turns (
    trace_id        text PRIMARY KEY,
    user_id         text NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    conversation_id uuid NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    message_id      text,
    sources         jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX chat_turns_conversation_idx ON chat_turns (conversation_id);
