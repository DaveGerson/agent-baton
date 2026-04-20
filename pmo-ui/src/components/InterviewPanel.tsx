import { useState } from 'react';
import { T, SR_ONLY, FONTS, SHADOWS } from '../styles/tokens';
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
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14, fontFamily: FONTS.body }}>

      {/* Header card */}
      <div style={{
        background: T.butter,
        border: `3px solid ${T.border}`,
        borderRadius: 14,
        boxShadow: SHADOWS.md,
        padding: '16px 20px',
        display: 'flex', alignItems: 'center', gap: 14,
      }}>
        <span style={{ fontSize: 36, lineHeight: 1 }}>{'🛎'}</span>
        <div>
          <div style={{
            fontFamily: FONTS.body, fontWeight: 800, fontSize: 10,
            textTransform: 'uppercase', letterSpacing: '0.12em',
            color: T.text1, marginBottom: 2,
          }}>
            A few questions from the pass
          </div>
          <div style={{
            fontFamily: FONTS.display, fontWeight: 900, fontSize: 26, color: T.text0, lineHeight: 1.1,
          }}>
            Help us sharpen the recipe
          </div>
          <div style={{ fontSize: 12, color: T.text2, marginTop: 3, fontFamily: FONTS.body }}>
            Answer what you can — unanswered questions use sensible defaults.
          </div>
        </div>
      </div>

      {/* Question cards */}
      {questions.map((q, i) => (
        <div key={q.id} style={{
          background: T.bg1,
          borderRadius: 12,
          border: `2px solid ${T.border}`,
          boxShadow: SHADOWS.md,
          padding: '14px 16px',
        }}>
          {/* Question header */}
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, marginBottom: 8 }}>
            {/* Question number badge */}
            <div style={{
              width: 26, height: 26, borderRadius: '50%',
              background: T.cherry, color: T.cream,
              fontFamily: FONTS.display, fontWeight: 900, fontSize: 13,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              flexShrink: 0, border: `2px solid ${T.border}`,
            }}>
              {i + 1}
            </div>
            <span style={{
              fontFamily: FONTS.display, fontWeight: 800, fontSize: 17, color: T.text0,
              lineHeight: 1.25, paddingTop: 2,
            }}>
              {q.question}
            </span>
          </div>

          {/* Context / hint */}
          {q.context && (
            <div style={{
              fontFamily: FONTS.hand, fontSize: 15, color: T.text2,
              transform: 'rotate(-0.3deg)', display: 'inline-block',
              marginBottom: 10, marginLeft: 36,
            }}>
              "{q.context}"
            </div>
          )}

          {/* Answer input */}
          {q.answer_type === 'choice' && q.choices ? (
            <fieldset style={{ border: 'none', padding: 0, margin: '0 0 0 36px' }}>
              <legend style={{
                fontSize: 11,
                fontWeight: 700,
                color: T.text3,
                marginBottom: 6,
                padding: 0,
                fontFamily: FONTS.body,
              }}>
                Select an answer for: {q.question}
              </legend>
              <div role="radiogroup" style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {q.choices.map(choice => {
                  const isSelected = answers[q.id] === choice;
                  return (
                    <button
                      key={choice}
                      role="radio"
                      aria-checked={isSelected}
                      onClick={() => setAnswer(q.id, choice)}
                      style={{
                        padding: '5px 12px', borderRadius: 999,
                        border: `1.5px solid ${T.border}`,
                        background: isSelected ? T.mint : T.bg2,
                        color: isSelected ? T.cream : T.text1,
                        fontSize: 12, fontWeight: 800, cursor: 'pointer',
                        fontFamily: FONTS.body,
                        boxShadow: isSelected ? SHADOWS.sm : 'none',
                        transition: 'background 0.15s',
                      }}
                    >
                      {choice}
                    </button>
                  );
                })}
                <button
                  onClick={() => setAnswer(q.id, '')}
                  aria-label="Skip this question"
                  style={{
                    padding: '5px 12px', borderRadius: 999,
                    border: `1.5px solid ${T.border}`, background: 'transparent',
                    color: T.text3, fontSize: 12, fontWeight: 700,
                    cursor: 'pointer', fontFamily: FONTS.body,
                  }}
                >
                  skip
                </button>
              </div>
            </fieldset>
          ) : (
            <div style={{ marginLeft: 36 }}>
              <label
                htmlFor={`interview-answer-${q.id}`}
                style={SR_ONLY}
              >
                {q.question}
              </label>
              <input
                id={`interview-answer-${q.id}`}
                type="text"
                value={answers[q.id] ?? ''}
                onChange={e => setAnswer(q.id, e.target.value)}
                placeholder="Type your answer..."
                style={{
                  width: '100%', padding: '9px 11px', borderRadius: 8,
                  border: `2px solid ${T.border}`, background: T.bg3,
                  color: T.text0, fontSize: 13, fontWeight: 600,
                  outline: 'none', fontFamily: FONTS.body,
                  boxShadow: 'inset 2px 2px 0 0 rgba(0,0,0,.06)',
                  boxSizing: 'border-box',
                }}
              />
            </div>
          )}
        </div>
      ))}

      {/* Action row */}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <button
          onClick={handleSubmit}
          disabled={loading}
          style={{
            padding: '10px 22px', borderRadius: 10,
            border: `3px solid ${T.border}`,
            background: loading ? T.bg3 : T.cherry,
            color: loading ? T.text3 : T.cream,
            fontSize: 13, fontWeight: 800,
            cursor: loading ? 'not-allowed' : 'pointer',
            opacity: loading ? 0.6 : 1,
            fontFamily: FONTS.body,
            boxShadow: loading ? 'none' : SHADOWS.md,
          }}
        >
          {loading
            ? 'Re-generating...'
            : answeredCount === 0
              ? 'Re-generate with defaults'
              : `Re-generate with ${answeredCount} answer${answeredCount !== 1 ? 's' : ''}`}
        </button>
        <button
          onClick={onCancel}
          style={{
            padding: '9px 18px', borderRadius: 10,
            border: `2px solid ${T.border}`, background: 'transparent',
            color: T.text1, fontSize: 12, fontWeight: 700,
            cursor: 'pointer', fontFamily: FONTS.body,
            boxShadow: SHADOWS.sm,
          }}
        >
          Back to Plan
        </button>
      </div>
    </div>
  );
}
