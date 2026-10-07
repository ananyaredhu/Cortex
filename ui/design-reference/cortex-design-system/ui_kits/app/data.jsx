const ASSET = '../../assets/';
const PEOPLE = [
  { id: 'priya', name: 'Priya', role: 'Engineer', email: 'priya@companya.com', access: 'Member', team: 'Platform' },
  { id: 'maya', name: 'Maya', role: 'Manager', email: 'maya@companya.com', access: 'Admin', team: 'Product' },
  { id: 'dana', name: 'Dana', role: 'Security lead', email: 'dana@companya.com', access: 'Admin', team: 'Security' },
  { id: 'jordan', name: 'Jordan', role: 'Compliance', email: 'jordan@companya.com', access: 'Auditor', team: 'Legal' },
  { id: 'sam', name: 'Sam', role: 'Contractor', email: 'sam@contractor.io', access: 'Guest', team: 'Platform' },
].map(p => ({ ...p, src: ASSET + 'personas/' + p.id + '.svg' }));
const SOURCES = [
  { id: 'jira', name: 'Jira', icon: 'square-kanban', items: '2,318 issues', synced: '2 min ago', status: 'synced', on: true },
  { id: 'confluence', name: 'Confluence', icon: 'book-open', items: '1,204 pages', synced: '6 min ago', status: 'synced', on: true },
  { id: 'slack', name: 'Slack', icon: 'hash', items: '48 channels', synced: 'Live', status: 'synced', on: true },
  { id: 'drive', name: 'Google Drive', icon: 'hard-drive', items: '9,870 files', synced: '1 h ago', status: 'failed', on: true },
  { id: 'github', name: 'GitHub', icon: 'git-branch', items: '36 repos', synced: '12 min ago', status: 'synced', on: true },
  { id: 'notion', name: 'Notion', icon: 'notebook', items: '—', synced: 'Paused', status: 'paused', on: false },
];
const SUGGESTIONS = ['When does the Q3 launch ship?', 'Who owns SSO for contractors?', 'What changed in the retention policy?'];
const THREADS = ['Q3 launch date', 'SSO for contractors', 'Retention policy v4', 'Onboarding checklist'];
const ANSWER = {
  full: {
    text: [
      ['The Q3 launch is set for ', 'September 24', '. Maya moved it from the 17th after the security review flagged two open items on contractor SSO ', 1, '.'],
      ['Dana closed one of those items last week; the second, ', 'ENG-2041', ', is in review with Priya ', 2, '. The launch checklist in Confluence is otherwise green ', 3, '.'],
    ],
    sources: [{ label: 'Q3 launch plan', tool: 'Confluence', who: 'maya' }, { label: 'ENG-2041', tool: 'Jira', who: 'priya' }, { label: 'Launch checklist', tool: 'Confluence', who: 'dana' }],
    people: ['maya', 'dana', 'priya'],
  },
  guest: {
    text: [['The Q3 launch is planned for ', 'late September', '. The detailed plan lives in a space you don\u2019t have access to ', 1, '.']],
    sources: [{ label: '#launch', tool: 'Slack', who: 'maya' }],
    restricted: 2,
    people: ['maya'],
  },
};
