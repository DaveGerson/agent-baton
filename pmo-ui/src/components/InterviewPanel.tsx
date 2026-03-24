import { useState } from 'react';
import { T } from '../styles/tokens';
import type { InterviewQuestion, InterviewAnswer } from '../api/types';

interface InterviewPanelProps {
  questions: InterviewQuestion[];
  onSubmit: (answers: InterviewAnswer[]) => void;
  onCancel: () => void;
  loading?: boolean;
}

export function InterviewPanel({ questions, onSubmit, onCancel, loading }: InterviewPanelProps) {
  const [answers, setAnswers] = useState<Record<string, string>>({});

  function setAnswer(questionId: string, value: string) {
    setAnswers(prev => ({ ...prev, [questionId]: value }));
  }

  function handleSubmit() {
    const result: InterviewAnswer[] = Object.entries(answers)
      .filter(([, v]) => v.trim())
      .map(([questionId, answer]) => ({ question_id: questionId, answer }));
    onSubmit(result);
  }

  const answeredCount = Object.values(answers).filter(v => v.trim()).length;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{
        fontSize: 9,
        fontWeight: 700,
        color: T.yellow,
        textTransform: 'uppercase',
        letterSpacing: 0.5,
      }}>
        Refinement Questions
      </div>
      <div style={{ fontSize: 8, color: T.text3 }}>
        Answer what you can — unanswered questions use sensible defaults.
      </div>

      {questions.map((q, i) => (
        <div key={q.id} style={{
          background: T.bg1,
          borderRadius: 4,
          border: `1px solid ${T.border}`,
          padding: 10,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginBottom: 4 }}>
            <div style={{
              width: 16, height: 16, borderRadius: '50%',
              background: T.yellow + '20',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: 8, fontWeight: 700, color: T.yellow, flexShrink: 0,
            }}>
              {i + 1}
            </div>
            <span style={{ fontSize: 9, fontWeight: 600, color: T.text0 }}>{q.question}</span>
          </div>
          {q.context && (
            <div style={{ fontSize: 8, color: T.text3, marginBottom: 6, marginLeft: 20 }}>
              {q.context}
            </div>
          )}

          {q.answer_type === 'choice' && q.choices ? (
            <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginLeft: 20 }}>
              {q.choices.map(choice => (
                <button
                  key={choice}
                  onClick={() => setAnswer(q.id, choice)}
                  style={{
                    padding: '3px 8px', borderRadius: 3,
                    border: `1px solid ${answers[q.id] === choice ? T.accent + '66' : T.border}`,
                    background: answers[q.id] === choice ? T.accent + '15' : 'transparent',
                    color: answers[q.id] === choice ? T.accent : T.text2,
                    fontSize: 8, fontWeight: 600, cursor: 'pointer',
                  }}
                >
                  {choice}
                </button>
              ))}
              <button
                onClick={() => setAnswer(q.id, '')}
                style={{
                  padding: '3px 8px', borderRadius: 3,
                  border: `1px solid ${T.border}`, background: 'transparent',
                  color: T.text3, fontSize: 8, cursor: 'pointer',
                }}
              >
                skip
              </button>
            </div>
          ) : (
            <div style={{ marginLeft: 20 }}>
              <input
                type="text"
                value={answers[q.id] ?? ''}
                onChange={e => setAnswer(q.id, e.target.value)}
                placeholder="Type your answer..."
                style={{
                  width: '100%', padding: '4px 8px', borderRadius: 3,
                  border: `1px solid ${T.border}`, background: T.bg2,
                  color: T.text0, fontSize: 9, outline: 'none',
                }}
              />
            </div>
          )}
        </div>
      ))}

      <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
        <button
          onClick={handleSubmit}
          disabled={loading || answeredCount === 0}
          style={{
            padding: '6px 16px', borderRadius: 4, border: 'none',
            background: loading || answeredCount === 0 ? T.bg3 : `linear-gradient(135deg, ${T.yellow}, #d97706)`,
            color: '#fff', fontSize: 9, fontWeight: 700,
            cursor: loading || answeredCount === 0 ? 'not-allowed' : 'pointer',
            opacity: loading || answeredCount === 0 ? 0.6 : 1,
          }}
        >
          {loading ? 'Re-generating...' : `Re-generate with ${answeredCount} answer${answeredCount !== 1 ? 's' : ''}`}
        </button>
        <button
          onClick={onCancel}
          style={{
            padding: '5px 10px', borderRadius: 4,
            border: `1px solid ${T.border}`, background: 'transparent',
            color: T.text2, fontSize: 9, cursor: 'pointer',
          }}
        >
          Back to Plan
        </button>
      </div>
    </div>
  );
}
