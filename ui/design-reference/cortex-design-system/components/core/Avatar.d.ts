export interface AvatarProps {
  /** Persona SVG/PNG. Falls back to initials */
  src?: string;
  name?: string;
  /** px — 20, 28, 32, 40, 56, 96 */
  size?: number;
  /** Ink outline — use inside the context graph */
  ring?: boolean;
  shape?: 'circle' | 'rounded';
  style?: React.CSSProperties;
}
export declare function Avatar(props: AvatarProps): JSX.Element;