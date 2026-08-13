export class AnalyzerError extends Error {
  code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = "AnalyzerError";
    this.code = code;
  }
}
