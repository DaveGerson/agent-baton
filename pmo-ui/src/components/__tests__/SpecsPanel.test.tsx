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
});
