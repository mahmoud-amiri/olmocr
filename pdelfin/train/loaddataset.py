from transformers import (
    AutoProcessor,
    DataCollatorForSeq2Seq
)

from pdelfin.train.core.cli import make_cli
from pdelfin.train.core.config import TrainConfig
from tqdm import tqdm
from .utils import (
    make_dataset, TruncatingCollator
)

from torch.utils.data import DataLoader

def main():
    train_config = make_cli(TrainConfig)  # pyright: ignore
    
    processor = AutoProcessor.from_pretrained(train_config.model.name_or_path)
    train_dataset, valid_dataset = make_dataset(train_config, processor)    

    print("Training dataset........")
    print(train_dataset)
    print(train_dataset[0])
    print("\n\n")

    print("Validation dataset........")
    print(valid_dataset)
    print(valid_dataset[list(valid_dataset.keys())[0]][0])
    print("\n\n")

    print("Datasets loaded into hugging face cache directory")

    # data_collator = TruncatingCollator(
    #     max_length=4096
    # )

    # train_dataloader = DataLoader(train_dataset, batch_size=1, num_workers=4, shuffle=False, collate_fn=data_collator)
    # max_seen_len = 0
    # for index, entry in tqdm(enumerate(train_dataloader)):
    #     if index == 0:
    #         print(entry)
        
    #     num_input_tokens = entry["input_ids"].shape[1]
    #     max_seen_len = max(max_seen_len, num_input_tokens)

    #     print(max_seen_len)

if __name__ == "__main__":
    main()
