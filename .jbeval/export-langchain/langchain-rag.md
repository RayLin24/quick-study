# 检索增强生成（RAG）

> 说明：本教程的注册源为 https://github.com/langchain-ai/langchain，但它不是快照证据，因此我无法据此核实任何具体 API 名称、导入路径、版本或参数。下文所有代码均为教学抽象，请以官方文档为准。

## 1. 为什么需要 RAG

大语言模型的知识来自训练数据，存在两个常见问题：

- **知识截止**：模型无法知道训练之后发生的事情。
- **缺少私有知识**：模型没有读过企业内部的文档、制度或产品手册。

RAG（检索增强生成）把“检索”和“生成”组合起来：先从一个可更新的知识库中检索与问题最相关的片段，再把片段作为上下文交给模型，让模型基于这些片段作答。它的优点是无需重新训练模型，就能把新知识或私有知识引入回答。

## 2. 完整流程

一次典型的 RAG 调用包含以下步骤：

1. **加载**：从文件、数据库或网页读取原始文本。
2. **切分**：把长文本切成适合嵌入和检索的块（chunk）。
3. **向量化**：用嵌入模型把每个文本块变成向量。
4. **存储**：把向量和原始文本存入向量数据库。
5. **检索**：把用户问题向量化，在向量库中找出最相似的 top-k 文本块。
6. **生成**：把检索结果拼入提示词，让大模型生成回答。

下面逐一说明。

## 3. 文档加载

LangChain 的文档加载器（Document Loader）把不同来源的数据读成统一的 `Document` 对象。`Document` 通常包含：

- `page_content`：页面文本内容；
- `metadata`：来源、页码、标题等元信息。

示例（教学抽象，未从证据核实）：

```python
from langchain_community.document_loaders import TextLoader

loader = TextLoader('<你的文件路径>.txt')
documents = loader.load()

for doc in documents:
    print(doc.page_content[:100])
    print(doc.metadata)
```

如果知识库在网页或 PDF 中，通常有对应的加载器。具体类名和依赖包需要查阅当时的官方文档。

## 4. 文档切分

为什么不能把整本手册直接喂给模型？

- 模型上下文窗口有限；
- 长文本中只有一小部分与问题相关，检索粒度太粗会降低准确性；
- 嵌入模型对过长文本的表示质量通常也会下降。

常见做法是使用 `RecursiveCharacterTextSplitter`：先尝试用一组分隔符递归切分，并让相邻块之间保留一定重叠（`chunk_overlap`），减少被切断的语义。

示例（教学抽象）：

```python
from langchain.text_splitter import RecursiveCharacterTextSplitter

text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=500,
    chunk_overlap=50
)
chunks = text_splitter.split_documents(documents)
print(len(chunks))
```

`chunk_size` 是每块的最大字符数，`chunk_overlap` 是相邻块重叠的字符数。这两个值需要结合文档类型、嵌入模型和任务需要调参。

## 5. 向量化与存储

嵌入模型（Embeddings）把文本映射为向量；语义相近的文本在向量空间中距离更近。向量数据库或向量存储则负责保存这些向量，并在检索时做相似度搜索。

示例（教学抽象）：

```python
from langchain_community.embeddings import OpenAIEmbeddings
from langchain_community.vectorstores import FAISS

embedding_model = OpenAIEmbeddings()
vectorstore = FAISS.from_documents(chunks, embedding_model)
```

这里把 `chunks` 和嵌入模型传给 FAISS，一次性完成向量化、建索引和存储。FAISS 是内存型向量库，适合原型开发；生产环境可根据需要选择其他向量数据库。

## 6. 检索

把向量存储转成检索器（retriever），就可以用自然语言问题查询相关信息。

示例（教学抽象）：

```python
retriever = vectorstore.as_retriever(search_kwargs={'k': 4})
docs = retriever.invoke('公司年假政策是什么？')

for doc in docs:
    print(doc.page_content)
```

`k` 表示返回最相似的多少条文本块。`k` 太小可能漏掉关键信息，`k` 太大可能引入无关内容，需要按场景调整。

## 7. 生成答案

最常见的做法是把检索到的文本块全部放进提示词，再让大模型基于这些内容回答。这种“全部塞进去”的方式在 LangChain 中对应 `stuff` 链类型。

示例（教学抽象）：

```python
from langchain.chains import RetrievalQA
from langchain_openai import ChatOpenAI

llm = ChatOpenAI()
qa = RetrievalQA.from_chain_type(
    llm=llm,
    retriever=retriever,
    chain_type='stuff'
)
answer = qa.invoke('公司年假政策是什么？')
print(answer['result'])
```

也可以更显式地构建一个 LCEL 链，控制提示词模板：

```python
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough

template = '''请根据下面的资料回答问题。如果资料中没有答案，请直接说明“资料中没有相关信息”。

资料：
{context}

问题：
{question}
'''

prompt = ChatPromptTemplate.from_template(template)

def format_docs(docs):
    return '\n\n'.join(doc.page_content for doc in docs)

chain = (
    {'context': retriever | format_docs, 'question': RunnablePassthrough()}
    | prompt
    | llm
)

result = chain.invoke('公司年假政策是什么？')
print(result.content)
```

提示词模板决定了回答的边界：要求模型“只依据资料回答”，能减少编造；要求模型“资料不足时承认不知道”，能提高可信度。

## 8. 完整流程串起来

把上面的步骤合并起来，就是一个最小的 RAG 链路（教学抽象）：

```python
from langchain_community.document_loaders import TextLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.embeddings import OpenAIEmbeddings
from langchain_community.vectorstores import FAISS
from langchain.chains import RetrievalQA
from langchain_openai import ChatOpenAI

loader = TextLoader('<你的文件路径>.txt')
documents = loader.load()

text_splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
chunks = text_splitter.split_documents(documents)

embedding_model = OpenAIEmbeddings()
vectorstore = FAISS.from_documents(chunks, embedding_model)
retriever = vectorstore.as_retriever(search_kwargs={'k': 4})

qa = RetrievalQA.from_chain_type(
    llm=ChatOpenAI(),
    retriever=retriever,
    chain_type='stuff'
)
answer = qa.invoke('公司年假政策是什么？')
print(answer['result'])
```

## 9. 练习

1. 把同一份长文档分别用 `chunk_size=200` 和 `chunk_size=1000` 切分，观察检索结果有什么不同。
2. 检索结果不相关时，你会先调整 `chunk_size`、`chunk_overlap` 还是把 `k` 调大？为什么？
3. 如果知识库包含 PDF、网页和数据库三种来源，加载与切分流程分别需要做什么调整？

> 再次提醒：本文中的 LangChain 代码是教学抽象。由于提供的注册源（https://github.com/langchain-ai/langchain）不是快照证据，我无法确认这些 API 在当前版本中仍然可用；请以官方文档和仓库 README 为准。