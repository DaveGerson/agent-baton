import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { BeadGraphView } from '../BeadGraphView';
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

describe('BeadGraphView', () => {
  it('shows the empty state when no beads are provided', () => {
    render(<BeadGraphView beads={[]} />);
    expect(screen.getByTestId('bead-graph-empty')).toBeInTheDocument();
    expect(screen.queryByTestId('bead-node')).not.toBeInTheDocument();
  });

  it('renders one node per bead when data is supplied', () => {
    const beads = [
      bead({ bead_id: 'bd-1' }),
      bead({ bead_id: 'bd-2' }),
      bead({ bead_id: 'bd-3' }),
    ];
    render(<BeadGraphView beads={beads} />);
    const nodes = screen.getAllByTestId('bead-node');
    expect(nodes).toHaveLength(3);
    expect(screen.getByText('bd-1')).toBeInTheDocument();
    expect(screen.getByText('bd-3')).toBeInTheDocument();
  });

  it('fires onNodeClick when a node is clicked', () => {
    const onNodeClick = vi.fn();
    const beads = [bead({ bead_id: 'bd-click' })];
    render(<BeadGraphView beads={beads} onNodeClick={onNodeClick} />);
    fireEvent.click(screen.getByTestId('bead-node'));
    expect(onNodeClick).toHaveBeenCalledTimes(1);
    expect(onNodeClick.mock.calls[0][0].bead_id).toBe('bd-click');
  });

  it('filters by tag substring (case-insensitive)', () => {
    const beads = [
      bead({ bead_id: 'bd-arch', tags: ['architecture'] }),
      bead({ bead_id: 'bd-bug', tags: ['warning'] }),
      bead({ bead_id: 'bd-arch2', tags: ['Arch-Review'] }),
    ];
    render(<BeadGraphView beads={beads} />);
    const filter = screen.getByTestId('bead-graph-filter') as HTMLInputElement;
    fireEvent.change(filter, { target: { value: 'arch' } });
    const nodes = screen.getAllByTestId('bead-node');
    expect(nodes).toHaveLength(2);
    expect(screen.queryByText('bd-bug')).not.toBeInTheDocument();
    expect(screen.getByText('bd-arch')).toBeInTheDocument();
    expect(screen.getByText('bd-arch2')).toBeInTheDocument();
  });

  it('shows a no-matches message when the filter excludes all beads', () => {
    const beads = [bead({ tags: ['warning'] })];
    render(<BeadGraphView beads={beads} />);
    fireEvent.change(screen.getByTestId('bead-graph-filter'), {
      target: { value: 'no-such-tag' },
    });
    expect(screen.getByTestId('bead-graph-no-matches')).toBeInTheDocument();
  });
});
