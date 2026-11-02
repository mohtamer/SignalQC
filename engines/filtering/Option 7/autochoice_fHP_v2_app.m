function fHP=autochoice_fHP_v2_app(acc,DT,NA,mag,Pt,direction,fLP)
%Select fHP using the filtered time history and the pre-filtered FAS
%   acc: acceleration time series
%   DT: the sampling interval equal to the reciprocal of the sampling frequency(fc)
%   NA: the number of points
%   mag: Earthquake magnitude
%   Pt: the arrival time of P wave
%   direction: EW:1, NS:2, UD:3
%   fLP: the low-pass filter cutoff frequency has been selected
isfind = 0; % initialize "found/not found" flag
%Default thresholds for each constraint
yuzhi1=[0.0851,0.394,0.350,0.256,0.0000719,0.000812,0.411,0.0226,0.0230,0.00692];%Horizontal direction
yuzhi2=[0.0517,0.284,0.444,0.130,0.0000521,0.000673,0.736,0.0259,0.0290,0.00524];%Vertical direction
%################ FAS calculation ##############
% DC-MEAN
ADC=DC_mean(acc);
% Zerocrossing_Taper
AZT=ZC_TAPER(ADC,DT);
% Zero & FAS
band=0.2;
method=4;
[~,~,afreqs,FASs]=FAS_Smooth(DT,AZT,band,method);
hptrylist=afreqs(afreqs<=10);
lp=fLP;
for m=1:length(hptrylist)
    hptry=hptrylist(m);
    % Filter
    [~,~,~,~,~,DIS,~]=Filter(DT,AZT,hptry,lp,'A','F');
    [~,VEL_sp_c,DIS_sp_c,~,~,~,tend]=Filter(DT,AZT,hptry,lp,'C','F');
    % Current value calculation
    dis_filter_j_max=max(abs(DIS_sp_c));
    dis_filter_j_ratio=abs(DIS_sp_c(end))/dis_filter_j_max;
    dis_filter_pad_j=DIS;
    t_pad=((0:length(dis_filter_pad_j)-1)*DT)';
    d_end=floor(length(t_pad)-tend+2*NA*0.1);
    dis_filter_pad_j_k=polyfit(t_pad(floor(length(t_pad)-tend-NA*0.1):d_end),dis_filter_pad_j(floor(length(t_pad)-tend-NA*0.1):d_end),1);
    dis_filter_pad_j_kabs=abs(dis_filter_pad_j_k(1));
    for mm=1:length(FASs)-9
        if afreqs(mm)>hptry
            polypoint(:,1)=afreqs(mm:mm+9);
            polypoint(:,2)=FASs(mm:mm+9);
            break
        end
    end
    pp=polyfit(log10(polypoint(:,1)),log10(polypoint(:,2)),1);
    Ptnumber=floor(0.95*Pt/DT);
    PGD_c=max(abs(DIS_sp_c));

    can_m(1)=abs(VEL_sp_c(end));
    can_m(2)=dis_filter_j_ratio;
    can_m(3)=dis_filter_pad_j_kabs;
    can_m(4)=pp(1);
    can_m(5)=abs(mean(DIS_sp_c(1:Ptnumber,1)))/PGD_c/PGD_c;
    can_m(6)=std(DIS_sp_c(1:Ptnumber,1))/PGD_c;
    %Check whether the threshold is met
    if mag<7
        if direction~=3
            if can_m(1)<=yuzhi1(1) & can_m(2)<=yuzhi1(3)...
                    & can_m(3)<=yuzhi1(5)...
                    & can_m(5)<=yuzhi1(7)...
                    & can_m(6)<=yuzhi1(9)
                if can_m(4)<=3 & can_m(4)>=1 | mag>=6
                    fHP=hptry;
                    isfind=1;
                    break
                end
            end
        else
            if can_m(1)<=yuzhi1(2) & can_m(2)<=yuzhi1(4)...
                    & can_m(3)<=yuzhi1(6)...
                    & can_m(5)<=yuzhi1(8)...
                    & can_m(6)<=yuzhi1(10)
                if can_m(4)<=3 & can_m(4)>=1 | mag>=6
                    fHP=hptry;
                    isfind=1;
                    break
                end
            end
        end
    else
        if direction~=3
            if can_m(1)<=yuzhi2(1) & can_m(2)<=yuzhi2(3)...
                    & can_m(3)<=yuzhi2(5)...
                    & can_m(5)<=yuzhi2(7)...
                    & can_m(6)<=yuzhi2(9)
                fHP=hptry;
                isfind=1;
                break
            end
        else
            if can_m(1)<=yuzhi2(2) & can_m(2)<=yuzhi2(4)...
                    & can_m(3)<=yuzhi2(6)...
                    & can_m(5)<=yuzhi2(8)...
                    & can_m(6)<=yuzhi2(10)
                fHP=hptry;
                isfind=1;
                break
            end
        end
    end
end
%If there is no frequency that meet all the conditions return -999
if isfind==0
    fHP=-999;
end

fHP=round(fHP,4);
end

