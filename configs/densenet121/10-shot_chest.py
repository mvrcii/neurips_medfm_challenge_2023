_base_ = [
    '../datasets/chest.py',
    '../schedules/chest.py',
    'mmpretrain::_base_/default_runtime.py',
    'mmpretrain::_base_/models/densenet/densenet121.py',
    '../custom_imports.py',
]

# Pre-trained Checkpoint Path
checkpoint = 'https://download.openmmlab.com/mmclassification/v0/densenet/densenet121_4xb256_in1k_20220426-07450f99.pth'  # noqa

lr = 5e-3
train_bs = 8
val_bs = 128
dataset = 'chest'
model_name = 'densenet121'
exp_num = 1
nshot = 10

run_name = f'{model_name}_bs{train_bs}_lr{lr}_exp{exp_num}'
work_dir = f'work_dirs/{dataset}/{nshot}-shot/{run_name}'

model = dict(
    head=dict(
        type='MultiLabelLinearClsHead',
        num_classes=19,
        in_channels=1024,
        loss=dict(type='CrossEntropyLoss', loss_weight=1.0)))

train_pipeline = [
    dict(type='LoadImageFromFile'),
    dict(type='RandomResizedCrop', scale=256, crop_ratio_range=(0.7, 1.0)),
    dict(type='RandomFlip', prob=0.5, direction='horizontal'),
    dict(type='RandomFlip', prob=0.5, direction='vertical'),
    dict(type='PackInputs'),
]

test_pipeline = [
    dict(type='LoadImageFromFile'),
    dict(type='Resize', scale=256),
    dict(
        type='PackInputs',
        # `gt_label_difficult` is needed for VOC evaluation
        meta_keys=('sample_idx', 'img_path', 'ori_shape', 'img_shape',
                   'scale_factor', 'flip', 'flip_direction',
                   'gt_label_difficult')),
]

train_dataloader = dict(
    batch_size=train_bs,
    dataset=dict(ann_file=f'data_anns/MedFMC/{dataset}/{dataset}_{nshot}-shot_train_exp{exp_num}.txt',
                 pipeline=train_pipeline)
)

val_dataloader = dict(
    batch_size=val_bs,
    dataset=dict(ann_file=f'data_anns/MedFMC/{dataset}/{dataset}_{nshot}-shot_val_exp{exp_num}.txt',
                 pipeline=test_pipeline)
)

test_dataloader = dict(
    batch_size=8,
    dataset=dict(ann_file=f'data_anns/MedFMC/{dataset}/test_WithLabel.txt',
                 pipeline=test_pipeline)
)

default_hooks = dict(
    checkpoint=dict(type='CheckpointHook', interval=250, max_keep_ckpts=1, save_best="auto", rule="greater"),
    logger=dict(interval=4),
)

optimizer = dict(lr=lr)

visualizer = dict(type='Visualizer', vis_backends=[dict(type='TensorboardVisBackend')])

train_cfg = dict(by_epoch=True, val_interval=25, max_epochs=1000)
