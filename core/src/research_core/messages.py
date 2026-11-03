"""The OpenAI SDK owns the user-message schema."""

from openai.types.chat import ChatCompletionUserMessageParam
from pydantic import RootModel, model_serializer, model_validator


class UserMessage(RootModel[ChatCompletionUserMessageParam]):
    @model_serializer
    def serialize(self):
        return self.root

    @model_validator(mode="after")
    def nonempty(self):
        content = self.root["content"]
        if not isinstance(content, str):
            content = list(content)
            self.root["content"] = content
            for part in content:
                if part["type"] == "file" and (
                    bool(part["file"].get("file_id")) == bool(part["file"].get("file_data"))
                ):
                    raise ValueError("A file needs exactly one of file_id or file_data")
        if not content:
            raise ValueError("A message must contain text or attachments")
        if isinstance(content, str) and len(content) > 50000:
            raise ValueError("Message text exceeds 50000 characters")
        return self
