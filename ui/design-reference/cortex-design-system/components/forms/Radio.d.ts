export interface RadioProps {
  options: (string | { value: string; label: string; description?: string })[];
  value?: string;
  defaultValue?: string;
  onChange?: (value: string) => void;
  name?: string;
}
export declare function Radio(props: RadioProps): JSX.Element;