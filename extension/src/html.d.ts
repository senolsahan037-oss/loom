declare module "*.html" {
  const content: string;
  export default content;
}

declare module "sensei:runtime" {
  const files: Record<string, string>;
  export default files;
}
