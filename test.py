import torch
from skimage import io
import matplotlib.pyplot as plt
import warnings
import matplotlib
from utils.Use_model import Use_model
import torchvision
import torchvision.transforms.transforms as T
from torch.utils.data import DataLoader
import configparser
import os
import pdb
import cv2
import numpy as np
from utils.loss_fn import *
import sys
import torch.nn.functional as F
import time
from sklearn import metrics
import yaml
from ptflops import get_model_complexity_info
import argparse

'''
主要測試model使用的檔案

進行測試時需要修改val_config內配置。
1. Method 1 (快速設置方法):
    快速設置參數：MOTHER_FOLDER。MOTHER_FOLDER直接傳入ModelResult資料夾即可使用。
    show_image: 是否顯示影像
    scale: 模型輸出是否經過sigmoid調整
    save_pred_binary: 是否直接儲存二值化影像
2. Method 2 (手動設置方法):
    training_setting_path: 訓練配置檔案(.yaml)位置 
    model_path: 模型權重/紀錄位置
    test_dataset_path: 測試資料集。D:\Programming\AI&ML\(Dataset)STU-Hospital
    save_path: 輸出影像儲存路徑
    show_image: 是否顯示影像
    scale: 模型輸出是否經過sigmoid調整
    save_pred_binary: 是否直接儲存二值化影像

本實驗之測試目標：
    
    1. F1 score
    2. mIoU (mean In different classes)
    3. Dice (mean In different classes)
    4. Inference time
    5. Parameters
    6. FLOPS
    
    可新增目標
    (1). 特異度
    (2). 敏感度


已棄用: 
binarization_th = 111 進行二值化之閥值
binarization = 0 是否進行二值化
'''
'內部資料測試：'\
r"D:\Programming\AI&ML\(Dataset)breast Ultrasound lmage Dataset\archive\InterTEST"
'外部資料測試：'\
r"D:\Programming\AI&ML\(Dataset)STU-Hospital"
'快速資料測試：' \
r"D:\Programming\AI&ML\(Dataset)breast Ultrasound lmage Dataset\archive\val_opt"

reject = {'null', 'Null', 'No', 'no', 'n', '0', 'None', 'none', 'False'}

def args_parser(n_fold):
    parser = argparse.ArgumentParser()
    with open('./val_config.ini') as fp:
        # source code from https://blog.csdn.net/wozaiyizhideng/article/details/107821713
        cfg = configparser.ConfigParser()
        cfg.read_file(fp)
        section_list = cfg.sections() # 讀取段落, 此處段落包含：model_set
        # print(section_list)
        for section in section_list:
            options_list = cfg.options(section)
            items_list = cfg.items(section)
            # print(options_list, items_list, sep='\t')
        MOTHER_FOLDER = cfg.get('model_set', 'MOTHER_FOLDER') # 快速配置文件使用。表示為Result的資料夾，為None時沒有效果
        model_path = cfg.get('model_set', 'model_path') # 獲取config文件中model_set的model_path值
        training_setting_path = cfg.get('model_set', 'training_setting_path')
        test_dataset_path = cfg.get('model_set', 'test_dataset_path')
        scale = cfg.getboolean('model_set', 'scale') # 取得bool
        save_path = cfg.get('model_set', 'save_path')
        show_image = cfg.getboolean('model_set', 'show_image')
        save_pred_binary = cfg.getboolean('model_set', 'save_pred_binary')
        log_file_path = cfg.get('model_set', 'test_log')

    if MOTHER_FOLDER not in reject:
        print('---MOTHER_FOLDER---')
        model_path = os.path.join(MOTHER_FOLDER, f'model_fold_{n_fold}.pth')
        training_setting_path = os.path.join(MOTHER_FOLDER, 'training setting.yaml')
        save_path = os.path.join(MOTHER_FOLDER, 'test_files')
        log_file_path = os.path.join(MOTHER_FOLDER, f'fold_{n_fold}_test_log.txt')

    # raise NotImplemented('還沒寫完!!')
    def _process_main(fname):
        import logging, pprint
        logging.basicConfig()
        logger = logging.getLogger()
        params = None
        with open(fname, 'r') as y_file:
            params = yaml.load(y_file, Loader=yaml.FullLoader)
            logger.info('loaded params...')
            pp = pprint.PrettyPrinter(indent=4)
            # pp.pprint(params)
        # dump = os.path.join(fr'{params["save"]["direc"]}', 'training setting.yaml')
        # with open(dump, 'w') as f: # 寫入檔案
        #     yaml.dump(params, f)
        return params
    test_args = _process_main(training_setting_path)

    parser.add_argument('--Test_name', type=str, default=test_args['meta']['Name'])
    parser.add_argument('-mp','--model_path', type=str, default=model_path)
    parser.add_argument('-mn','--modelname', default=test_args['meta']['modelname'], type=str)
    parser.add_argument('--batchsize', default=1, type=int)
    parser.add_argument('-tdp','--test_dataset_path', type=str, default=test_dataset_path, help='測試資料集位置')
    parser.add_argument('--load_state_dict', type=bool, default=True, help='是否只載入權重，默認載入權重')
    parser.add_argument('--save_path', type=str, default=save_path, help='圖片儲存位置')
    parser.add_argument('-is', '--imgsize', type=int, default=test_args['data']['imgsize'], help='圖片大小')
    parser.add_argument('--imgchan', type=int, default=test_args['data']['imgchan'], help='輸入影像通道(預設3)')
    parser.add_argument('-ic', '--classes', type=int, default=test_args['data']['classes'], help='model輸出影像通道數(grayscale)')
    parser.add_argument('--device', default=test_args['meta']['device'], help='是否使用GPU訓練')
    # mask傳入設定
    parser.add_argument('--ds_mask', type=bool, default=True, help='資料集是否含有mask')
    parser.add_argument('--ds_mask_gray', type=bool, default=True, help='mask是否輸出灰階')
    # pred輸出設定
    parser.add_argument('--scale', type=bool, default=scale, help='pred輸出是否要經過sigmoid scale')
    parser.add_argument('--deep_supervise', type=bool, default=test_args['optimization']['deep_supervise'], help='使用深層監督')
    parser.add_argument('--show_image', type=bool, default=show_image, help='show_image')
    parser.add_argument('--save_pred_binary', type=bool, default=save_pred_binary, help='')
    # 測試(數值)結果儲存
    parser.add_argument('--log_file_path', type=str, default=log_file_path, help='測試(數值)結果儲存路徑')



    args = parser.parse_args()

    return args
class test_dataloader(DataLoader):
    '''
    建立test的dataloader
    載入測試圖片格式：
    -test_dataset
        -images
            -image1
            -image2
            ...
        -masks
            -mask1
            -mask2
            ...
        ...
    '''
    def __init__(self, valid_ds, args=None):
        super().__init__(valid_ds)
        img_ds = os.path.join(valid_ds,'images')
        if args.ds_mask:
            mask_ds = os.path.join(valid_ds, 'masks')
            self.masks_list = [os.path.join(mask_ds, file) for file in os.listdir(mask_ds)]
        self.images_list = [os.path.join(img_ds, file) for file in os.listdir(img_ds)]
        self.imgsize = args.imgsize
        self.device = args.device
        self.ds_mask = args.ds_mask
        self.ds_mask_gray = args.ds_mask_gray
    def __len__(self):
        return len(self.images_list)
    def __getitem__(self, index):
        image = cv2.imread(self.images_list[index])
        original_img_size = image.shape  # 原始image大小，H,W,3
        mask_out, original_mask_size = torch.zeros(original_img_size), original_img_size
        # resize完後改回原本model能接受的尺寸(B,C,H,W)
        image = cv2.resize(image, (self.imgsize,self.imgsize), interpolation=cv2.INTER_NEAREST)
        # 輸入影像值為[0-1]之間，不然輸出結果會異常
        image = np.transpose(image, (2, 0, 1)) / 255.
        # image = np.transpose(image, (2, 1, 0)) / 255. # TEST，之前測試輸出影像方向錯誤之改正方法
        # print('Test dataset size：', image.shape,sep='\n')
        # 回傳沒有經過資料增量的影像+原圖尺寸(tuple)
        img_out = torch.tensor(image).to(self.device).to(torch.float32)
        if self.ds_mask:
            if not self.ds_mask_gray:
                mask = cv2.imread(self.masks_list[index])
            mask = cv2.imread(self.masks_list[index], 0)
            original_mask_size = mask.shape  # 原始mask大小，H,W,C
            mask = cv2.resize(mask, (self.imgsize,self.imgsize), interpolation=cv2.INTER_NEAREST)
            mask = mask / 255
            mask_out = torch.tensor(mask).to(self.device).to(torch.int)
            mask_out = mask_out.view(1, *mask_out.shape) # to match metrics fn
            # mask_out = mask_out.view(1, *mask_out.shape).permute(0,2,1) # TEST，之前測試輸出影像方向錯誤之改正方法

        return img_out, mask_out, (original_img_size,original_mask_size)
def calculate(model_out, mask):
    '''注意輸入的影像數值是否為[0,255]'''
    iou = IoU(model_out, mask.cuda())
    f1_s = classwise_f1(model_out, mask.cuda(), testing=True)  # 這邊有用
    Dice = 1 - dice_loss(mask, model_out, )
    return f1_s.cpu().detach().numpy().astype(float), iou.cpu().detach().numpy(), Dice.cpu().detach().numpy()
def adaptiveThreshold(img):
    img = img[:,:,0].numpy() * 255
    img = img.astype(np.uint8)
    assert type(img)==np.ndarray, f'{type(img)}'
    img = cv2.medianBlur(img,5)
    th1 = cv2.adaptiveThreshold(img,255,cv2.ADAPTIVE_THRESH_MEAN_C,cv2.THRESH_BINARY,11,2)
    th2 = cv2.adaptiveThreshold(img,255,cv2.ADAPTIVE_THRESH_GAUSSIAN_C,cv2.THRESH_BINARY,255,2)
    return th1,th2
def appointed_threshold(pred, th=0.333):
    '指定數值之二值化方法'
    # 輸出影像未經過標準化，此處為標準化程序
    th = pred.max()*th - pred.min() * (th - 1)

    pred = pred[:,-1,]
    pred = (pred > th).float()
    return pred

def plot_roc_curve(fper, tper):
    plt.plot(fper, tper, color='red', label='ROC')
    plt.plot([0, 1], [0, 1], color='green', linestyle='--')
    plt.xlabel('False Positive Rate')
    plt.ylabel('True Positive Rate')
    plt.title('Receiver Operating Characteristic Curve')
    plt.legend()
    plt.show()
def main(n_fold):
    args = args_parser(n_fold)
    dataloader = test_dataloader(args.test_dataset_path, args=args)
    dataloader = DataLoader(dataloader)
    # 載入模型
    model = Use_model(args)
    model.load_state_dict(torch.load(args.model_path)) # 載入權重
    model.eval()
    f1_final, iou_final, Dice_final, AUC_final = 0., 0., 0., 0.
    infer_time = 0
    macs, params = get_model_complexity_info(model, (3, 128, 128), as_strings=True, print_per_layer_stat=False, verbose=False)

    for i, (image ,mask, size) in enumerate(dataloader):
        assert type(image) == torch.Tensor, f'correct type is torch.Tensor, now is {type(image)}'
        assert image.shape == (args.batchsize, 3, args.imgsize, args.imgsize), f'correct:{image.shape}, should be (1,3,h,w)'# confirm input format
        graph_num = 2
        time_start = time.time()
        pred = model(image.cuda())
        infer_time += time.time() - time_start
        if args.scale:
            pred = sigmoid_scaling(pred) # 使用sigmoid歸一化
        # 二值化----指定threshold值
        gussan_th = appointed_threshold(pred).reshape(128,128).cpu().clone().detach()
        # 自適應二值化
        pred = pred.to('cpu').detach().view(pred.shape[1],pred.shape[2],pred.shape[3],).permute(1, 2, 0)  # h,w,2
        # mean_th, gussan_th = adaptiveThreshold(pred)
        # gussan_th = 255 - gussan_th
        # pred[:,:,0] = 255 - pred[:,:,0]

        # ---------------------略過---------------------------
        # TEST ROC (本實驗是影像分割問題，而不是分類問題，應該不需要用到ROC曲線。)
        # BUT 先保留此寫法，預防未來用到。
        p = gussan_th.reshape(128,128).cpu().numpy()
        m = mask.reshape(128,128).cpu().numpy()
        fper, tper, thresholds = metrics.roc_curve(m.flatten(),p.flatten(), pos_label=1)
        auc = metrics.auc(fper, tper)
        # plot_roc_curve(fper, tper)
        # ---------------------以下繼續---------------------------

        f1, iou, Dice = calculate(gussan_th.reshape(1,1,args.imgsize, args.imgsize).clone().detach().cuda(), mask)
        f1_final += f1
        iou_final += iou
        Dice_final += Dice
        AUC_final += auc
        graph_num += 1

        fig = plt.figure()
        image = image.to('cpu').detach().view(image.shape[1],image.shape[2],image.shape[3],).permute(1, 2, 0)  # h,w,3

        fig.add_subplot(2,2,1)
        plt.xticks([]), plt.yticks([])  # 關閉座標刻度
        # plt.axis('off')
        plt.title('GT')  # 1*3的圖片 的 第1張
        plt.imshow(mask.cpu().reshape(128,128), alpha=0.5)
        plt.imshow(image, alpha=0.5)

        fig.add_subplot(2, 2, 2)
        plt.xticks([]), plt.yticks([])  # 關閉座標刻度
        # plt.axis('off')
        plt.title('binary_blend_gussan')
        plt.imshow(gussan_th, alpha=0.4)
        plt.imshow(image, alpha=0.3)
        plt.imshow(mask.cpu().reshape(128,128), alpha=0.3)
        if args.save_pred_binary:
            bi_folder_path = os.path.join(args.save_path, 'bi_pred_file')
            os.makedirs(bi_folder_path) if os.path.exists(bi_folder_path) is False else reject
            f_name = os.path.join(bi_folder_path, f'{i} pred.png')
            cv2.imwrite(f_name, gussan_th)

        ax = fig.add_subplot(2, 2, 3)
        plt.xticks([]), plt.yticks([])
        # plt.axis('off')
        plt.title('image')
        plt.imshow(image)

        ax = fig.add_subplot(2, 2, 4)
        plt.xticks([]), plt.yticks([])
        # plt.axis('off')
        plt.title('pred')
        ax.set_xlabel('IoU: {:4f}, Dice: {:4f}, AUC: {:4f}'.format(iou, Dice, auc))
        plt.imshow(pred[:,:,-1], alpha=0.5)
        plt.imshow(image, alpha=0.5)


        save_path = os.path.join(args.save_path, f'{i}')
        plt.savefig(save_path)
        if args.show_image:
            plt.show()
        plt.clf()
        plt.close('all')
        continue
    # print('F1 score TEST data: ',f1_final/(i+1),',', 'mIoU TEST data:', iou_final/(i+1), f'Dice TEST data: {Dice_final/(i+1)}')
    # print('avg inferance time:', infer_time/(i+1),)

    # Log writer 記錄測試紀錄
    with open(args.log_file_path, 'w') as L:
        n = i
        L.write(f'Record time: {time.asctime()}\n\n')
        L.write(f'Test name: {args.Test_name}\n\n')
        L.write(f'Model name: {args.modelname}\n\n')
        L.write(f'Parameters: {params}\nComputational complexity: {macs}\n\n')
        L.write(f'=========== Result ============\n\n')
        L.write(f'Mean F1 score (ON TEST DATA): \n{f1_final.__float__()/(n+1)}\n\n')
        L.write(f'Mean mIoU  (ON TEST DATA): \n{iou_final/(i+1)}\n\n')
        L.write(f'Mean Dice  (ON TEST DATA): \n{Dice_final/(i+1)}\n\n')
        L.write(f'Mean AUC  (ON TEST DATA): \n{AUC_final/(i+1)}\n\n')
        L.write(f'Avg inferance time: \n{infer_time/(i+1)}\n\n')
        L.close()


if __name__ == '__main__':
    n_fold = 5
    for i in range(n_fold):
        main(i+1)


