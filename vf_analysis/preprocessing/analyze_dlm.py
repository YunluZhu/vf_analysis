'''
Analyze Free Vertical (YZ 2020.05.28)
- Functions:
    1. Takes one raw dataframe
    2. Truncate
    3. Calculate and filter for epoch duration, displacement, headx-x moving direction, angular velocity, angular acceleration 
    4. Add a distance filter
    5. Return an analyzed dataframe and a fish length dataframe
- Includes: 
    1. analyzeFreeVerticalGrouped2 (by DEE)
    2. angular acceleration filter in GrabFishAngelAll
- Purpose of Python version by YZ:
    1. Rewrite of analyzeFreeVerticalGrouped2.m & GrabFishAngleAll.m for faster runtime (5s per .dlm)
    3. Adjust filters:
- Notes:
    1. Results of filtered epochs may be slightly different from Matlab results, due to the angVel smooth function
        Matlab smooth handles top/end values differently. Here, top values without enought window (smooth span) are returned as NA
    2. Due to the float64 data type, calculations are more accurate in Python version.
'''
# %%
# Import Modules and functions
import pandas as pd # pandas library
import numpy as np # numpy
from datetime import datetime
from datetime import timedelta

# %%
# Constants
FRAM_RATE = 40      # Hz
FRAME_INTERVAL = 1 / FRAM_RATE
MIN_DUR = 2.5 * FRAM_RATE  # 2.5s, minimun duration of epochs 
MAX_FISH = 1         # all epochs that have more than one fish
MAX_INST_DISPL = 35  # epochs where fish# > 1 but appear as 1 fish will have improbably large instantaneous displacement.
MAX_ANG_VEL = 100  # or an improbably large angular velocity
MAX_ANG_ACCEL = 32000  # or an improbably large angular accel.
MAX_DELTA_T = 0.05   # epochs with inexplicably large gaps between frame timestamps
MAX_DIST_TRAVEL = 26 # max adjusted distance traveled value. defined as: (dist-dist.rolling(3, center=True).median()).abs(), epochs with multiple fish but appeared as 1 fish have aberrent displ jumps - YZ 20.05.13
MIN_VERTICLE_VEL = -5 # (mm/s) max verdical displacement difference. Used to exclude fish bout downwards.
EPOCH_BUF = 2        # truncate the epochs from both ends. In Matlab code, 3 was excluded in analyzeFreeVerticalGrouped2 and another 5 was dropped in GrabFishAngel
# However, in analyzeFreeVerticalGrouped2, line epochDex:epochStop(i):epochStop(i+1) incorrectly truncated the beginning of the epoch by 1 and the end by 3. 

# Other parameters
SCALE = 60           #(pix/mm) ofr BlackFly verticle fish rigs 1-6
SM_WINDOW_FOR_FILTER = 9     # smoothing
SM_WINDOW_FOR_ANGVEL = 3

# %%
# Define functions
def grp_by_epoch(df):
    # Group df by 'epochNum'. If cols is empty take all columns
    return df.groupby('epochNum', sort=False)

def smooth_series_ML(a,WSZ):
    '''
    Modified from Divakar's answer https://stackoverflow.com/questions/40443020/matlabs-smooth-implementation-n-point-moving-average-in-numpy-python
    a: NumPy 1-D array containing the data to be smoothed
    WSZ: smoothing window size needs, which must be odd number,
    as in the original MATLAB implementation
    '''
    out0 = np.convolve(a,np.ones(WSZ,dtype=int),'valid')/WSZ    
    r = np.arange(1,WSZ-1,2)
    start = np.cumsum(a[:WSZ-1])[::2]/r
    stop = (np.cumsum(a[:-WSZ:-1])[::2]/r)[::-1]
    res = np.concatenate((  start , out0, stop  ))
    return pd.Series(data=res, index=a.index)

# define filter function
def raw_filter(df):
    # Trim epoch by EPOCH_BUF and filter by duration & fish number
    # First, group by epoch number
    grouped = grp_by_epoch(df)
    # Truncate epoch, see EPOCH_BUF for details
    # use .cumcount() to return indices within group
    del_buf = df[(grouped.cumcount(ascending=False) >= EPOCH_BUF) 
        & (grouped.cumcount() >= EPOCH_BUF)]
    # Flter by epoch duration & number of fish in the frame
    filtered = grp_by_epoch(del_buf).filter(
        lambda g: len(g) >= MIN_DUR and 
        np.nanmax(g['fishNum'].values) < MAX_FISH
    )
    print(".", end = '')
    return filtered

def dur_y_x_filter(df):
    # drop epochs with inexplicably large gaps between frame
    f1 = grp_by_epoch(df).filter(
        lambda g: np.nanmax(g['deltaT'].values) <= MAX_DELTA_T
    # turned off in Kyla's version
    #     # exclude fish bouts vertically down (sinking faster than 5mm/sec). 
    #     # Flip the sign so positive deltaY corresponds to upward motion
    #     and -(np.nanmax(np.diff(g['y'].values, prepend=g['y'].values[0]))) > MIN_VERTICLE_VEL * FRAME_INTERVAL * SCALE
    )
    # only keep swims in which fish is pointed in the direction it moves. Within an epoch, if headx is greater than x (pointing right), x.tail should also be greater than x.head, and vice versa.
    f2 = grp_by_epoch(f1).filter(
        lambda g: ((g['headx'].mean() - g['x'].mean()) * g.tail(1)['x'].values)[0] >= 0
    )
    print(".", end='')
    return f2

def displ_dist_vel_filter(df):
    # drop epochs with improbably large instantaneous displacement, which happens where #fish > 1 but appear as 1 fish
    f1 = grp_by_epoch(df).filter(
        lambda g: np.nanmax(np.absolute(g['displ'].values)) <= MAX_INST_DISPL
        # exclude epochs with sudden & large instantaneous movement (distance). Found in ~1-2 epochs per .dlm after MAX_INST_DISPL filtration - YZ 2020.05.13
        # turn off vor now
        and np.nanmax(np.abs(
            g['dist'].values - g['dist'].rolling(3, center=True).median().values
        )) < MAX_DIST_TRAVEL   
    )
    # exclude epochs with improbably large angular velocity. use smoothed results
    f2 = grp_by_epoch(f1).filter(
        lambda g: np.nanmax(np.abs(
            smooth_series_ML(g.loc[1:,'angVel'], SM_WINDOW_FOR_FILTER).values
        )) <= MAX_ANG_VEL
    )
    # exclude epochs with improbably large angular accel. numpy calculation is faster
    f3 = grp_by_epoch(f2).filter(
        lambda g: np.nanmax(np.abs(g['angAccel'].values)) <= MAX_ANG_ACCEL
    )
    print(".", end="")
    return f3

# %%
# Main function
def analyze_dlm(raw, file_i, file, folder):
    
    # truncate epochs
    raw_truncate = raw_filter(raw).reset_index().rename(columns={'index': 'oriIndex'})
    
    # %%
    # Calculate time/date and Filter for delta t

    # Initialize dataframe for analyzed epochs
    ana = pd.DataFrame()
    # Transfer original index, epochNum, and time info
    ana = raw_truncate[['oriIndex','epochNum','ang','absy']].copy()
    # Calculate time difference
    # use .assign() for assigning new columns, avoid df[['newCol]] or df.loc[:,'newCol']
    ana = ana.assign(
        deltaT = grp_by_epoch(raw_truncate).time.diff()
    )
    # Get the start time from file name
    datetime_frmt = '%y%m%d %H.%M.%S'
    time_stamp = file[-19:-4]
    start_time = datetime.strptime(time_stamp, datetime_frmt)
    # Calculate absolute datetime for each timepoint. DataFrame calculation is faster than using .apply()
    ana.insert(1,
        'absTime', start_time + pd.to_timedelta(raw_truncate['time'].values, unit=('s'))
    )

    # Calculate coordinates
    #   dataframe subduction is faster than cumulative difference. 
    #   Use .values to convert into arrays to further speed up
    centered_coordinates = pd.DataFrame((
        raw_truncate[['absx','absy','absHeadx','absHeady','ang']].values
        - grp_by_epoch(raw_truncate)[['absx','absy','absHeadx','absHeady','ang']].transform('first').values
    ), columns = ['x','y','headx','heady','centeredAng'])

    ana = ana.join(centered_coordinates)

    # Apply filters
    ana_f = dur_y_x_filter(ana)

    # %%
    # Calculate displacement, distance traveled, angular velocity, angular acceleration and filter epochs

    ana_f_g = grp_by_epoch(ana_f)
    ana_f = ana_f.assign(
        # x and y velocity. using np.divide() has shorter runtime than df.div()
        xvel = np.divide(ana_f_g['x'].diff().values, ana_f['deltaT'].values),
        yvel = np.divide(ana_f_g['y'].diff().values, ana_f['deltaT'].values),
        # use numpy function np.linalg.norm() for displacement and distance
        dist = np.linalg.norm(ana_f_g[['x','y']].diff(), axis=1),
        # since beginning coordinates for each epoch has been set to 0, just use (x, y) values for displ
        displ = ana_f.groupby('epochNum', as_index=False, sort=False).apply(
            lambda g: pd.Series((np.linalg.norm(g[['x','y']], axis=1)),index = g.index).diff()
        ).reset_index(level=0, drop=True),
        # array calculation is more time effieient
        angVel = np.divide(ana_f_g['ang'].diff().values, ana_f['deltaT'].values)
    )
    # now let's get smoothed angular vel and angular acceleration
    ana_f = ana_f.assign(  
        # loop through each epoch, get second to last angVel values (exclude the first one which is NA)
        # calculate smooth, keep the index, assign to the new column
        angVelSmoothed = pd.concat(
            smooth_series_ML(g.tail(len(g)-1)['angVel'],SM_WINDOW_FOR_ANGVEL) for i, g in grp_by_epoch(ana_f)
        ),
        angAccel = np.divide(grp_by_epoch(ana_f)['angVel'].diff().values, ana_f['deltaT'].values),
    )

    # Apply filters, drop previous index
    ana_ff = displ_dist_vel_filter(ana_f).reset_index(drop=True)

    # Acquire fish length from raw data
    ana_ff['fishLen'] = raw.loc[ana_ff['oriIndex'],'fishLen'].values

    # %%
    # SCALE distance and velocity and tansfer data we care

    res = ana_ff.copy()
    # SCALE coordinate and displ, flip signs of y, positive = upwards
    res.loc[:,['y','heady','yvel']] = res[['y','heady','yvel']] * -1 / SCALE
    res.loc[:,['x','headx','xvel','displ','dist','fishLen']] = res[['x','headx','xvel','displ','dist','fishLen']] / SCALE
    # calculate swim speed
    res.loc[:,'swimSpeed'] = np.divide(res['dist'].values, res['deltaT'].values)
    # calculate swim velocity (displacement/)
    res.loc[:,'velocity'] = np.divide(res['displ'].values, res['deltaT'].values)
    # define fish length as 70th percentile of lengths captured.
    fish_length = grp_by_epoch(res)['fishLen'].agg(
        fishLenEst = lambda l: l.quantile(0.7)
    ).reset_index()

    # %%
    # Save analyzed data!

    # res.to_pickle(f'{folder}/{file_i+1}_analyzed_epochs.pkl')
    # fish_length.to_pickle(f'{folder}/{file_i+1}_fish_length.pkl')
    print(f" {len(grp_by_epoch(res).size())} epochs extracted", end=' ')

    return res, fish_length

