import torch
from torch.utils.data import DataLoader,SubsetRandomSampler
from torch import no_grad
import os
from torch.cuda.amp import GradScaler, autocast
import time
from torch.utils.tensorboard import SummaryWriter
import warnings
import torch.nn.functional as F
import shutil
from torchvision.utils import save_image
from sklearn.model_selection import KFold
import numpy as np

from utils.Dataloader_breastUS import ImageToImage2D,Image2D,JointTransform2D
from utils import loss_fn, Use_model
from utils.Use_model import *
from show_img import Save_image
from utils import Other_utils as OU

import argparse
import yaml
from ptflops import get_model_complexity_info


'''
Train script ver1.0

此model script為訓練模型所編寫，啟動時需要透過windows powershell啟動
使用時需要選擇使用的model名稱、loss函數、訓練週期等參數

注意事項:
pass

更新紀錄:
    tarin ver1.0。
        2022/4/18
        1. 加入 k_fold 訓練機制: Limitation: k_fold設定不可為1。
    train ver1.0.1
        2022/4/19
        1. yaml功能。新增資料讀取方法
        2. 資料視覺化與計算f1 score, iou計算影像相同
        3. 新增deep supervise功能。都用if else來寫，目前非常不好維護，後面需要修改。
    train ver1.1.0
        2022/4/27
        1. 更改程式架構: 分成Optimizer、K Fold、Training部分，增加註解
        2. init_training_result_folder與save_model_mode移到新檔案Other_utils中
        3. 目前問題: 模型進入第二個fold以後結果不良
    train ver1.1.1
        1. 加入訓練初始化，解決train ver1.1.0之第二個fold結果不良

'''

print('Training script version 1.1.0', 'Last edit in 20220427', sep='\n')

def main(args):
    save_freq = args.save_freq
    args.use_autocast = bool(args.use_autocast)
    gray = False if args.imgchan == 3 else True
    train_dataset = ImageToImage2D(args.train_dataset, img_size=(args.imgsize, args.imgsize), get_catagory=args.catagory, Gray=gray)
    # train_dataset = DataLoader(train_dataset, batch_size=args.batch_size, shuffle=True)
    # val_dataset = Image2D(args.val_dataset, img_size=(args.imgsize, args.imgsize))
    # val_dataset = DataLoader(val_dataset)
    splits = KFold(n_splits=int(args.k_fold), shuffle=True, random_state=42)  # 設定random_state使輸出都一樣

    # if args.run_formal:
    #     OU.Double_check_training_setting()
    model = Use_model(args)
    criteriwe = use_loss_fn(args)
    optimizer = use_opt(args, model)
    scheduler = use_scheduler(args, optimizer)
    writer = SummaryWriter(f'{args.direc}/log')
    time_start = time.time()
    use_autocast = f'{"="*10} USE autocast! {"="*10}' if args.use_autocast else f'{"="*10} NO autocast! {"="*10}'
    # warnings.warn(use_autocast)

    f_val_loss, f_f1, f_iou = 0.0, 0.0, 0.0
    for fold, (train_idx, val_idx) in enumerate(splits.split(train_dataset)):
        print('----- Fold {} -----'.format(fold + 1))
        train_sampler = SubsetRandomSampler(train_idx)
        test_sampler = SubsetRandomSampler(val_idx)
        train_loader = DataLoader(train_dataset, batch_size=args.batch_size, sampler=train_sampler)
        val_loader = DataLoader(train_dataset, batch_size=1, sampler=test_sampler)
        one_fold_start_time = time.time()
        for i, epoch in enumerate(range(args.epoch)):
            loss = train_one_epoch(args,
                                   dataloader=train_loader,
                                   model=model,
                                   optimizer=optimizer,
                                   scheduler=scheduler,
                                   lossfn=criteriwe)
            writer.add_scalar(f'training {args.loss_fn} loss', scalar_value=loss, global_step=i+(args.epoch*fold))
            if i % save_freq == 0:
                assert save_freq > 1, 'save_freq只能設定大於1。'
                val_dataset = Image2D(args.val_dataset, img_size=(args.imgsize, args.imgsize), Gray=gray)
                final_val_dataset = DataLoader(val_dataset)
                folder_name = fr'{args.direc}/val_images/fold{fold+1}_epoch{epoch+1}'
                save_model_name = f'{args.direc}/model_fold_{fold + 1}_{epoch+1}.pth'
                val_loss, f1, iou = eval(final_val_dataset, model, folder_name,
                                         binarization=True,
                                         scaling=True, # must set scaling and binarization
                                         save_valid_img=args.save_valid_img,
                                         lossfn=criteriwe,
                                         save_model=False,
                                         save_model_name=save_model_name)
                writer.add_scalar(f'fold_{fold} val_loss', scalar_value=val_loss, global_step=i)
                writer.add_scalar(f'fold_{fold} f1 score', scalar_value=f1, global_step=i)
                writer.add_scalar(f'fold_{fold} mIoU score', scalar_value=iou, global_step=i)
            if i + 1 == args.epoch:
                print('=' * 10, 'last one eval', '=' * 10)
                val_dataset = Image2D(args.val_dataset, img_size=(args.imgsize, args.imgsize), Gray=gray)
                final_val_dataset = DataLoader(val_dataset)
                folder_name = fr'{args.direc}/val_images/fold{fold+1}_epoch{epoch+1}'
                save_model_name = f'{args.direc}/model_fold_{fold + 1}.pth'
                val_loss, f1, iou = eval(val_loader, model, folder_name,
                                         binarization=True,
                                         scaling=True,
                                         save_valid_img=True,
                                         lossfn=criteriwe,
                                         save_model=args.savemodel,
                                         save_model_name=save_model_name)
                f_val_loss += val_loss
                f_f1 += f1
                f_iou += iou
                one_fold_end_time = time.time()
                writer.add_scalar(f'fold Training time',
                                  scalar_value=one_fold_end_time - one_fold_start_time,
                                  global_step=fold+1)
                print('=' * 10, 'last one eval finish!!!!!', '=' * 10)
                #
                # ---------- 模型初始化 ------------
                model = Use_model(args)
                criteriwe = use_loss_fn(args)
                optimizer = use_opt(args, model)
                scheduler = use_scheduler(args, optimizer)
                # -------------- Finish --------------
                break
    f_val_loss, f_f1, f_iou = f_val_loss/(fold+1), f_f1/(fold+1), f_iou/(fold+1)
    print('f_val_loss:{:8f}, f_f1:{:8f}, f_iou:{:8f}'.format(f_val_loss.item(), f_f1.item(), f_iou.item()))

    time_end = time.time()
    print('training Done! Use {:.2f} s'.format(time_end - time_start))

def train_one_epoch(args, dataloader, model, lossfn, optimizer, scheduler):
    scaler = GradScaler(enabled=args.use_autocast)
    model.to(args.device)
    model.train()
    train_accumulation_steps = args.train_accumulation_steps
    for i, (image, mask) in enumerate(dataloader):
        image = image.to(args.device) if args.device == 'cuda:0' else image
        mask = mask.to(args.device) if args.device == 'cuda:0' else mask
        with autocast(enabled=args.use_autocast):
            f_loss = 0.
            if args.deep_supervise:
                # Use deep supervise
                output, *f = model(image)
                f = f[0]
                f = f.clone().detach().requires_grad_(True)
                f_loss = deep_supervise(f, mask=mask, lossfn=lossfn)
            else:
                output = model(image)
            loss = (lossfn(output, mask) + f_loss) / train_accumulation_steps
        if args.use_autocast:
            FutureWarning('AMP is ON, precision will a little down')
            scaler.scale(loss).backward(retain_graph=True)
            scaler.step(optimizer)
            scaler.update()
        if not args.use_autocast:
            loss.backward()
            if i % train_accumulation_steps == 0:
                optimizer.step()
                optimizer.zero_grad()
        if not args.run_formal and i == 2:
            print('----Not formal training!----')
            scheduler.step(loss)
            return loss
        if i + 1 == len(dataloader):
            scheduler.step(loss)
            return loss


def eval(val_dataset, model, folder_name, lossfn, binarization=False, scaling=False,
         save_valid_img=False, save_model=False, save_model_name=None):
    model.eval()
    model.to('cuda')
    print('start eval!!!')
    save_path = folder_name
    test_loss, f1, iou = 0., 0., 0.

    original_size = torch.tensor((args.imgsize,args.imgsize))

    for i, (original_image, mask) in enumerate(val_dataset):
        original_image = original_image.to('cuda') if torch.cuda.is_available() else original_image
        x = original_image.to(torch.float32)
        with no_grad():
            if args.deep_supervise:
                pred, *_ = model(x) # b,c,h,w
            else:
                pred = model(x) # b,c,h,w
            pred = pred.to('cpu')
            # Use loss function
            if scaling:
                pred = loss_fn.sigmoid_scaling(pred)

            if save_valid_img:
                if not os.path.exists(save_path):
                    os.makedirs(save_path)
                Save_image(original_image.to('cpu'), pred, mask,
                           save_path=fr'{save_path}/num{i + 1}',
                           original_size=original_size,
                           channel=args.classes,
                           resize=args.savefig_resize
                           )
            if binarization:
                # <Solved 已解決>用我自己設計的Unet跑無法產生影像，問題應該在閥值設定的問題。
                th = pred.max() * args.threshold - pred.min() * (args.threshold - 1)
                pred = (pred > th).float()
                # pred = (pred > args.threshold).float()  # 1, 1, 128, 128
                # pred = OU.THRESH_BINARY_for_pred(pred, return_tensor=True)
            test_loss += lossfn(pred, mask)
            f1 += loss_fn.classwise_f1(pred, mask)
            iou += loss_fn.IoU(pred, mask)
            del pred

    if save_model and save_model_name:
        OU.save_model_mode(model, save_model_name)
    val_loss = test_loss / len(val_dataset)
    f1 = f1 / len(val_dataset)
    iou = iou / len(val_dataset)
    print(f'epoch validation.',
          'avg_eval_loss：{:.4f}, '
          'f1 score：{:.4f},'
          ' mIoU score：{:.4f}'.format(test_loss,f1.item(),iou.item()),
          sep='\t')
    return val_loss, f1, iou

    # =============================額外增加功能放在這邊=============================
def deep_supervise(*feature, mask, lossfn):
    '''
    feature: 輸入影像之特徵(tuple of tensor), Any size of feature.
        default (B,C,H,W): (8, 16, 32, 32)
        default(N,B,C,H,W): ((8,128,32,32),(8,64,64,64))
    mask:
        default(B,1,H,W)

    :return f_loss (scalar)
    '''
    # f_weight = torch.nn.Parameter(torch.randn(len(feature))) #可學習權重
    f_weight = torch.ones(len(feature)) #不可學習權重
    f_loss = []
    for f in feature:
        b,c,h,w = mask.shape
        f = f.to(args.device) if args.device == 'cuda:0' else f
        f = F.interpolate(f, size=(h,w), mode='bilinear', align_corners=True)
        f_loss.append(lossfn(f, mask))

    f_loss = torch.mul(f_weight, torch.tensor(f_loss)).sum()
    return f_loss
def parser_args(model_name=None):
    'yaml test'
    parser = argparse.ArgumentParser(description=' Version')
    parser.add_argument('--fname', type=str, help='name of config file to load', default=r'.\config\train_config.yaml')
    def _process_main(fname):
        import logging, pprint
        logging.basicConfig()
        logger = logging.getLogger()
        params = None
        with open(fname, 'r') as y_file:
            params = yaml.load(y_file, Loader=yaml.FullLoader)
            logger.info('loaded params...')
            pp = pprint.PrettyPrinter(indent=4)
            pp.pprint(params)
        os.makedirs(params["save"]["direc"]) if os.path.exists(params["save"]["direc"]) is False else print('folder is exist')
        return params
    absFilePath = os.path.abspath(__file__)
    fname = os.path.join(os.path.split(absFilePath)[0],'config/train_config.yaml')
    args = _process_main(fname)
    print(args['meta']['modelname'])
    if args["save"]["run_formal"]:
        OU.Double_check_training_setting()
        OU.init_training_result_folder(args["save"]["direc"])
    dump = os.path.join(fr'{args["save"]["direc"]}', 'training setting.yaml')
    with open(dump, 'w') as f: # 寫入檔案
        print('writing!!')
        yaml.dump(args, f)
    # Training parameter setting
    parser.add_argument('--epoch', default=args['optimization']['epochs'], type=int, help='需要跑的輪數')
    parser.add_argument('-bs', '--batch_size', default=args['optimization']['batchsize'], type=int)
    parser.add_argument('-is', '--imgsize', type=int, default=args['data']['imgsize'], help='圖片大小')
    parser.add_argument('-ic', '--imgchan', type=int, default=args['data']['imgchan'], help='使用資料的通道數，預設3(RGB)')
    parser.add_argument('-class', '--classes', type=int, default=args['data']['classes'], help='model輸出影像通道數(grayscale)')
    parser.add_argument('-model', '--modelname', default=args['meta']['modelname'], type=str)
    parser.add_argument('-ds_path', '--train_dataset', default=fr'{args["data"]["ds_path"]}', type=str, help='訓練資料集位置')
    parser.add_argument('-vd', '--val_dataset', type=str, default=args['data']['val_dataset'], help='驗證用資料集所在位置')
    parser.add_argument('--catagory', type=int, default=args['data']['catagory'], help='使用類別資料與否。如果使用，將輸出正常0，有腫瘤1')



    # Model training setting
    parser.add_argument('--device', type=str, default=args['meta']['device'], help='是否使用GPU訓練')
    parser.add_argument('-ds', '--dataset', choices=['BreastUS'], default=args['data']['dataset'],
                        help='選擇使用的資料集，默認GS，預設BreastUS')
    parser.add_argument('--use_autocast', type=bool, default=args['meta']['use_autocast'], help='是否使用混和精度訓練')
    parser.add_argument('--threshold', type=int, default=args['save']['threshold'],
                        help='設定model output後二值化的threshold, 介於0-1之間')
    parser.add_argument('--train_accumulation_steps', default=args['optimization']['train_accumulation_steps'],
                        type=int, help='多少iters更新一次權重(可減少顯存負擔)')
    parser.add_argument('--k_fold', type=int, default=args['optimization']['k_fold'], help='使用k_fold訓練')
    parser.add_argument('--deep_supervise', type=bool, default=args['optimization']['deep_supervise'], help='使用深層監督')
    parser.add_argument('--pos', type=bool, default=args['optimization']['pos'], help='位置編碼')


    # Optimizer Setting
    parser.add_argument('--lr', type=float, default=args['optimization']['lr'], help='learning rate')
    parser.add_argument('--scheduler', type=str, default=args['criterion']['scheduler'], help='使用的scheduler')
    parser.add_argument('-opt', '--optimizer', type=str, default=args['criterion']['optimizer'], help='使用的optimizer')
    parser.add_argument('--weight_decay', type=float, default=args['optimization']['weight_decay'], help='Optimizer weight decay')


    # Loss function and Loss schedule
    parser.add_argument('-loss', '--loss_fn', type=str, default=args['criterion']['loss'],
                        choices=['wce', 'diceloss', 'IoU', 'FocalLoss', 'bce', 'lll', 'clsiou'])
    parser.add_argument('-wce', '--wce_beta', type=float, default=1e-04, help='wce_loss的wce_beta值，如果使用wce_loss時需要設定')

    # Save Setting
    parser.add_argument('-sf', '--save_freq', type=int, default=args['save']['save_frequency'],
                        help='多少個epoch儲存一次checkpoint')
    parser.add_argument('--save_state_dict', type=bool, default=args['save']['save_state_dict'],
                        help='是否只儲存權重，默認為權重')
    parser.add_argument('--savemodel', type=bool, default=args['save']['savemodel'], help='是否儲存模型')
    parser.add_argument('-r', '--run_formal', type=bool, default=args['save']['run_formal'],
                        help='是否是正式訓練(if not, train 8 iters for each epoch)')
    parser.add_argument('--direc', type=str, default=args['save']['direc'], help='directory to save')
    parser.add_argument('--savefig_resize', type=bool, default=args['save']['savefig_resize'], help='savefig resize')
    parser.add_argument('--save_valid_img', type=bool, default=args['save']['save_valid_img'], help='save validation result(in every save freq)')

    args = parser.parse_args()

    return args

if __name__ == '__main__':
    args = parser_args()
    main(args)