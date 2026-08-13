export class DiagramRendererError extends Error {
  code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = "DiagramRendererError";
    this.code = code;
  }
}
