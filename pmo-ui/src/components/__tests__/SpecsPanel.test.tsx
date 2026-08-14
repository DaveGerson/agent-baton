import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { SpecsPanel } from '../SpecsPanel';
import { api } from '../../api/client';

afterEach(() => {
  vi.restoreAllMocks();
});

describe('SpecsPanel', () => {
  it('survives a bare-array response from GET /pmo/specs (spec-queue shape)', async () => {
    // The spec-queue router owns GET /api/v1/pmo/specs and returns a bare
    // list[SpecDraftResponse]; this panel was written against a
    // {specs: [...]} envelope. Regression for the shape mismatch that
    // crashed the whole app at mount (specs became undefined, then
    // specs.find threw during render with no error boundary above).
    vi.spyOn(api, 'listSpecs').mockResolvedValue([] as never);

    render(<SpecsPanel onBack={() => {}} />);

    expect(await screen.findByText(/0 specs/)).toBeInTheDocument();
  });

  it('renders specs from the {specs: [...]} envelope shape', async () => {
    vi.spyOn(api, 'listSpecs').mockResolvedValue({
      specs: [
        {
          spec_id: 'spec-1',
          title: 'sample spec',
          state: 'draft',
          task_type: 'feature',
          created_at: '2026-01-01T00:00:00Z',
          updated_at: '2026-01-01T00:00:00Z',
        },
      ],
    } as never);

    render(<SpecsPanel onBack={() => {}} />);

    expect(await screen.findByText(/1 spec\b/)).toBeInTheDocument();
  });

  it('renders specs from the bare-array shape the production backend returns', async () => {
    // GET /api/v1/specs is served by agent_baton/api/routes/specs.py::list_specs,
    // which is declared `-> list[dict[str, Any]]` and returns
    // `[s.to_dict() for s in specs]` — a bare JSON array. This is the ONLY
    // shape a real deployment ever produces, so a non-empty array must be
    // rendered, not silently dropped.
    vi.spyOn(api, 'listSpecs').mockResolvedValue([
      {
        spec_id: 'spec-aaa',
        project_id: 'default',
        author_id: 'ada',
        task_type: 'feature',
        template_id: 'feature',
        title: 'real spec one',
        state: 'approved',
        content: 'goal: ship it',
        content_hash: 'deadbeef',
        score_json: '{}',
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-02T00:00:00Z',
        approved_at: '2026-01-02T00:00:00Z',
        approved_by: 'grace',
        linked_plan_ids: [],
      },
      {
        spec_id: 'spec-bbb',
        project_id: 'default',
        author_id: 'grace',
        task_type: 'bug-fix',
        template_id: 'bug-fix',
        title: 'real spec two',
        state: 'draft',
        content: 'goal: fix it',
        content_hash: 'cafebabe',
        score_json: '{}',
        created_at: '2026-01-03T00:00:00Z',
        updated_at: '2026-01-04T00:00:00Z',
        approved_at: '',
        approved_by: '',
        linked_plan_ids: [],
      },
    ] as never);

    render(<SpecsPanel onBack={() => {}} />);

    // Header counter reflects what the backend actually returned.
    expect(await screen.findByText(/2 specs/)).toBeInTheDocument();

    // Both specs are visible as rows.
    expect(screen.getByText('real spec one')).toBeInTheDocument();
    expect(screen.getByText('real spec two')).toBeInTheDocument();
    expect(screen.getAllByRole('listitem')).toHaveLength(2);

    // And the panel must NOT claim the store is empty — the silent-failure
    // symptom this pins is a confident, error-free "no specs" screen.
    expect(screen.queryByText(/No specs yet/)).not.toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
});
