import { useState } from 'react';
import type { CSSProperties, FormEvent } from 'react';
import { T, FONTS, FONT_SIZES, SHADOWS } from '../styles/tokens';
import { api } from '../api/client';
import type { CRPRequestBody, CRPResponse } from '../api/types';

/**
 * H3.9 — Change Request Process (CRP) wizard.
 *
 * A single-page form that captures a structured change request and
 * posts it to `POST /pmo/crp`. The endpoint currently returns a
 * synthesized plan summary (the planner integration is wired
 * separately) so this view ships immediately.
 */

const RISK_LEVELS: CRPRequestBody['risk_level'][] = ['low', 'medium', 'high', 'critical'];

const SUGGESTED_AGENTS = [
  'architect',
  'backend-engineer',
  'frontend-engineer',
  'data-engineer',
  'devops-engineer',
  'security-reviewer',
  'auditor',
];

export function CRPWizard() {
  const [title, setTitle] = useState('');
  const [scopeText, setScopeText] = useState('');
  const [rationale, setRationale] = useState('');
  const [riskLevel, setRiskLevel] = useState<CRPRequestBody['risk_level']>('medium');
  const [agent, setAgent] = useState<string>('architect');

  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<CRPResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setResult(null);
    setSubmitting(true);
    try {
      const body: CRPRequestBody = {
        title: title.trim(),
        scope: scopeText
          .split(/[\n,]/)
          .map((s) => s.trim())
          .filter(Boolean),
        rationale: rationale.trim(),
        risk_level: riskLevel,
        suggested_agent: agent,
      };
      const res = await api.submitCrp(body);
      setResult(res);
    } catch (err) {
      setError(String((err as Error).message ?? err));
    } finally {
      setSubmitting(false);
    }
  }

  const containerStyle: CSSProperties = {
    padding: 16,
    background: T.bg0,
    color: T.text0,
    fontFamily: FONTS.body,
    maxWidth: 720,
  };

  const fieldStyle: CSSProperties = {
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
    marginBottom: 12,
  };

  const labelStyle: CSSProperties = {
    fontSize: FONT_SIZES.sm,
    color: T.text1,
    fontWeight: 700,
  };

  const inputStyle: CSSProperties = {
    padding: '6px 10px',
    fontFamily: FONTS.body,
    fontSize: FONT_SIZES.md,
    border: `2px solid ${T.border}`,
    borderRadius: 4,
    background: T.cream,
    color: T.text0,
  };

  const submitStyle: CSSProperties = {
    background: T.accent,
    color: T.cream,
    border: `2px solid ${T.border}`,
    borderRadius: 4,
    padding: '8px 18px',
    fontWeight: 700,
    cursor: submitting ? 'wait' : 'pointer',
    fontFamily: FONTS.body,
    fontSize: FONT_SIZES.md,
    boxShadow: SHADOWS.sm,
  };

  return (
    <div style={containerStyle} data-testid="crp-wizard">
      <h1 style={{ fontFamily: FONTS.display, fontSize: 24, margin: 0 }}>
        File a Change Request
      </h1>
      <div style={{ color: T.text2, fontSize: FONT_SIZES.sm, marginBottom: 16 }}>
        Captures the title, scope, rationale, and risk-level so the planner can
        forge an appropriate plan.
      </div>

      <form onSubmit={handleSubmit}>
        <div style={fieldStyle}>
          <label htmlFor="crp-title" style={labelStyle}>Title</label>
          <input
            id="crp-title"
            data-testid="crp-title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            required
            style={inputStyle}
          />
        </div>

        <div style={fieldStyle}>
          <label htmlFor="crp-scope" style={labelStyle}>
            Scope (files / paths, comma- or newline-separated)
          </label>
          <textarea
            id="crp-scope"
            data-testid="crp-scope"
            value={scopeText}
            onChange={(e) => setScopeText(e.target.value)}
            rows={3}
            style={inputStyle}
          />
        </div>

        <div style={fieldStyle}>
          <label htmlFor="crp-rationale" style={labelStyle}>Rationale</label>
          <textarea
            id="crp-rationale"
            data-testid="crp-rationale"
            value={rationale}
            onChange={(e) => setRationale(e.target.value)}
            rows={3}
            style={inputStyle}
          />
        </div>

        <div style={{ display: 'flex', gap: 12 }}>
          <div style={{ ...fieldStyle, flex: 1 }}>
            <label htmlFor="crp-risk" style={labelStyle}>Risk Level</label>
            <select
              id="crp-risk"
              data-testid="crp-risk"
              value={riskLevel}
              onChange={(e) => setRiskLevel(e.target.value as CRPRequestBody['risk_level'])}
              style={inputStyle}
            >
              {RISK_LEVELS.map((r) => (
                <option key={r} value={r}>{r}</option>
              ))}
            </select>
          </div>

          <div style={{ ...fieldStyle, flex: 1 }}>
            <label htmlFor="crp-agent" style={labelStyle}>Suggested Lead Agent</label>
            <select
              id="crp-agent"
              data-testid="crp-agent"
              value={agent}
              onChange={(e) => setAgent(e.target.value)}
              style={inputStyle}
            >
              {SUGGESTED_AGENTS.map((a) => (
                <option key={a} value={a}>{a}</option>
              ))}
            </select>
          </div>
        </div>

        <button
          type="submit"
          style={submitStyle}
          disabled={submitting}
          data-testid="crp-submit"
        >
          {submitting ? 'Submitting...' : 'File Change Request'}
        </button>
      </form>

      {error && (
        <div
          role="alert"
          style={{
            marginTop: 16,
            color: T.cherry,
            border: `2px solid ${T.cherry}`,
            padding: 8,
            borderRadius: 4,
          }}
        >
          {error}
        </div>
      )}

      {result && (
        <div
          data-testid="crp-result"
          style={{
            marginTop: 16,
            background: T.cream,
            border: `2px solid ${T.border}`,
            padding: 12,
            borderRadius: 6,
            boxShadow: SHADOWS.sm,
          }}
        >
          <h2 style={{ marginTop: 0, fontFamily: FONTS.display }}>
            CRP Filed · {result.crp_id}
          </h2>
          <pre
            style={{
              fontFamily: FONTS.mono,
              fontSize: FONT_SIZES.sm,
              whiteSpace: 'pre-wrap',
              background: T.bg1,
              padding: 8,
              borderRadius: 4,
            }}
          >
            {result.plan_summary}
          </pre>
          <div style={{ marginTop: 8 }}>
            <strong>Suggested phases:</strong> {result.suggested_phases.join(' → ')}
          </div>
        </div>
      )}
    </div>
  );
}

export default CRPWizard;
