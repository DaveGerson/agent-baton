import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { BeadTimelineView } from '../BeadTimelineView';
import type { BeadNode } from '../../api/types';

function bead(overrides: Partial<BeadNode> = {}): BeadNode {
  return {
    bead_id: 'bd-001',
    bead_type: 'warning',
    agent_name: 'backend-engineer',
    content: 'sample content',
    status: 'open',
    created_at: '2026-01-01T00:00:00Z',
    tags: [],
    ...overrides,
  };
}

describe('BeadTimelineView', () => {
  it('shows the empty state when no beads are provided', () => {
    render(<BeadTimelineView beads={[]} />);
    expect(screen.getByTestId('bead-timeline-empty')).toBeInTheDocument();
    expect(screen.queryByTestId('bead-timeline-entry')).not.toBeInTheDocument();
  });

  it('renders one entry per bead', () => {
    const beads = [
      bead({ bead_id: 'bd-a' }),
      bead({ bead_id: 'bd-b' }),
      bead({ bead_id: 'bd-c' }),
      bead({ bead_id: 'bd-d' }),
    ];
    render(<BeadTimelineView beads={beads} />);
    const entries = screen.getAllByTestId('bead-timeline-entry');
    expect(entries).toHaveLength(4);
  });

  it('orders entries newest-first by created_at', () => {
    const beads = [
      bead({ bead_id: 'bd-old', created_at: '2025-01-01T00:00:00Z' }),
      bead({ bead_id: 'bd-new', created_at: '2026-06-01T00:00:00Z' }),
      bead({ bead_id: 'bd-mid', created_at: '2026-01-01T00:00:00Z' }),
    ];
    render(<BeadTimelineView beads={beads} />);
    const entries = screen.getAllByTestId('bead-timeline-entry');
    expect(within(entries[0]).getByText(/bd-new/)).toBeInTheDocument();
    expect(within(entries[1]).getByText(/bd-mid/)).toBeInTheDocument();
    expect(within(entries[2]).getByText(/bd-old/)).toBeInTheDocument();
  });

  it('fires onEntryClick when an entry is clicked', () => {
    const onEntryClick = vi.fn();
    const beads = [bead({ bead_id: 'bd-click' })];
    render(<BeadTimelineView beads={beads} onEntryClick={onEntryClick} />);
    fireEvent.click(screen.getByTestId('bead-timeline-entry'));
    expect(onEntryClick).toHaveBeenCalledTimes(1);
    expect(onEntryClick.mock.calls[0][0].bead_id).toBe('bd-click');
  });

  it('filters entries by tag (case-insensitive substring)', () => {
    const beads = [
      bead({ bead_id: 'bd-keep', tags: ['Knowledge'] }),
      bead({ bead_id: 'bd-drop', tags: ['warning'] }),
    ];
    render(<BeadTimelineView beads={beads} />);
    fireEvent.change(screen.getByTestId('bead-timeline-filter'), {
      target: { value: 'know' },
    });
    const entries = screen.getAllByTestId('bead-timeline-entry');
    expect(entries).toHaveLength(1);
    expect(within(entries[0]).getByText(/bd-keep/)).toBeInTheDocument();
  });
});
