const AGENT_DISPLAY_NAMES: Record<string, string> = {
  'backend-engineer': 'Backend',
  'backend-engineer--python': 'Python Backend',
  'backend-engineer--node': 'Node Backend',
  'frontend-engineer': 'Frontend',
  'frontend-engineer--react': 'React Frontend',
  'frontend-engineer--dotnet': '.NET Frontend',
  'test-engineer': 'Testing',
  'architect': 'Architecture',
  'security-reviewer': 'Security',
  'devops-engineer': 'DevOps',
  'data-engineer': 'Data Engineering',
  'data-analyst': 'Data Analysis',
  'data-scientist': 'Data Science',
  'code-reviewer': 'Code Review',
  'auditor': 'Audit',
  'subject-matter-expert': 'SME',
  'visualization-expert': 'Visualization',
};

export function agentDisplayName(raw: string): string {
  return AGENT_DISPLAY_NAMES[raw] ?? raw.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}
