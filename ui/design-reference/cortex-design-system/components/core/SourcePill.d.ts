/**
 * @startingPoint section="Components" subtitle="Mono entity pill — the brand's signature element" viewport="700x200"
 */
export interface SourcePillProps {
  /** Entity name: doc, ticket, channel, tool */
  label: string;
  /** Lucide icon replaces the orange square */
  icon?: string;
  size?: 'sm' | 'md' | 'lg';
  /** outline = ink border (graph/hero); soft = hairline (dense lists) */
  variant?: 'outline' | 'soft';
  /** Citation number — renders an orange numbered chip */
  index?: number;
  onClick?: () => void;
  style?: React.CSSProperties;
}
export declare function SourcePill(props: SourcePillProps): JSX.Element;