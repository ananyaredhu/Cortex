export interface AvatarGroupProps {
  people: { src?: string; name?: string }[];
  size?: number;
  max?: number;
}
export declare function AvatarGroup(props: AvatarGroupProps): JSX.Element;