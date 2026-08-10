"""RAG 检索模块（第二期）

子模块：
  - embedding: 本地 bge-small-zh embedding 单例
  - vectorstore: ChromaDB 封装
  - text_splitter: 中文切分（自己实现，不引 langchain-text-splitters）
  - retriever: 高层 search() 接口
  - ingest: 文档入库
"""
