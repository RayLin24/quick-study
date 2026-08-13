"""Static analysis of repository source code.

One document shape describes every language. Python is analysed in process with ``ast``
and ``symtable``; JavaScript and TypeScript are analysed by the ``@quick-study/ts-analyzer``
subprocess. Both emit the same structures, so downstream stages never branch on language.

No analyser executes the code it reads.
"""
