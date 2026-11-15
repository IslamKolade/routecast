import { RISK_COLOR, RISK_LABEL } from '../lib/risk';
import type { RiskLevel } from '../types';

export function RiskBadge({ level }: { level: RiskLevel }) {
  return (
    <span className="risk-badge" style={{ '--risk': RISK_COLOR[level] } as React.CSSProperties}>
      <span className="risk-badge__dot" />
      {RISK_LABEL[level]}
    </span>
  );
}
